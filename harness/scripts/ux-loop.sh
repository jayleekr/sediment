#!/usr/bin/env bash
# ux-loop.sh — inner UX iteration loop.
#
# Each iter:
#   1. Capture: run validator --phase P2 --only-layers L11 (E2E + screenshots)
#   2. Score: dispatch sediment-ux-critic on screenshots → feedback.json
#   3. Decide: if overall >= target_overall AND all axes >= target_per_axis: STOP
#   4. Fix: dispatch sediment-coder with top finding as work-order
#   5. Loop
#
# Cost-aware. State in output/ux/STATE.json.
#
# Usage:
#   bash ux-loop.sh                              # default: max-iter 30, cost $25
#   bash ux-loop.sh --max-iter 5                 # tighter
#   bash ux-loop.sh --cost-budget 10             # tighter cost
#   bash ux-loop.sh --duration 7200              # stop after 2 hours wall time
#   bash ux-loop.sh --dry-run                    # capture only, no critic/coder

set -uo pipefail

# Strip parent Claude env (LEARNINGS pattern=parent_session_env_inheritance_breaks_child)
unset CLAUDE_CODE_ENTRYPOINT CLAUDE_CODE_EXECPATH CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS \
      CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS CLAUDE_CODE_MAX_OUTPUT_TOKENS \
      CLAUDE_CODE_SSE_PORT CLAUDECODE \
      OTEL_EXPORTER_OTLP_ENDPOINT OTEL_RESOURCE_ATTRIBUTES OTEL_SERVICE_NAME 2>/dev/null || true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SVC="$REPO_ROOT/services/sediment"
UX_DIR="$REPO_ROOT/output/ux"
RUBRIC="$SVC/validator/ux_rubric.yaml"
LEARNINGS="$REPO_ROOT/harness/ralph/LEARNINGS.md"
STATE_FILE="$UX_DIR/STATE.json"

mkdir -p "$UX_DIR"

MAX_ITER="${MAX_ITER:-30}"
COST_BUDGET="${COST_BUDGET:-25}"
DURATION_S="${DURATION_S:-0}"      # 0 = no wall-clock cap
DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --max-iter)     MAX_ITER="$2"; shift 2 ;;
    --cost-budget)  COST_BUDGET="$2"; shift 2 ;;
    --duration)     DURATION_S="$2"; shift 2 ;;
    --dry-run)      DRY_RUN=1; shift ;;
    *)              echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(ts)] $*"; }

# Initialize state
if [ ! -f "$STATE_FILE" ]; then
  cat > "$STATE_FILE" <<EOF
{
  "version": 1,
  "iteration": 0,
  "started_at": "$(ts)",
  "stopped_at": null,
  "stop_reason": null,
  "max_iterations": $MAX_ITER,
  "cost_budget_usd": $COST_BUDGET,
  "duration_s": $DURATION_S,
  "cumulative_cost_usd": 0.0,
  "best_overall": 0,
  "best_iter": 0,
  "axis_history": []
}
EOF
fi

START_EPOCH=$(date +%s)
ITER=$(jq -r '.iteration // 0' "$STATE_FILE")

finish() {
  local reason="${1:-stopped}"
  jq --arg r "$reason" --arg t "$(ts)" \
    '.stop_reason = $r | .stopped_at = $t' \
    "$STATE_FILE" > "$STATE_FILE.tmp" && mv "$STATE_FILE.tmp" "$STATE_FILE"
  log "ux-loop stopped: $reason"
  exit 0
}

trap 'finish "interrupted"' INT TERM

# ============================================================
# Per-iter steps
# ============================================================

run_capture() {
  local iter="$1"
  log "iter=$iter capture: validator --phase P2 --only-layers L11"
  cd "$SVC"
  .venv/bin/python -m validator --phase P2 --only-layers L11 \
    > "$UX_DIR/iter-$(printf '%02d' "$iter").capture.log" 2>&1
  local rc=$?
  cd "$REPO_ROOT"
  if [ "$rc" -ne 0 ]; then
    log "  capture exit=$rc (E2E may have flakes — continuing)"
  fi
  # Find latest screenshot iter dir
  LATEST_SCREENSHOT_DIR=$(ls -1dt "$REPO_ROOT/output/validation/screenshots"/iter-* 2>/dev/null | head -1)
  log "  screenshot dir: $LATEST_SCREENSHOT_DIR"
  echo "$LATEST_SCREENSHOT_DIR"
}

run_critic() {
  local iter="$1"
  local screenshot_dir="$2"
  local out_path="$UX_DIR/iter-$(printf '%02d' "$iter").feedback.json"
  local prompt_file="$UX_DIR/iter-$(printf '%02d' "$iter").critic.prompt"
  local out_log="$UX_DIR/iter-$(printf '%02d' "$iter").critic.log"

  cat > "$prompt_file" <<EOF
You are now operating as **sediment-ux-critic**. Read your contract first:
  $REPO_ROOT/.claude/agents/sediment-ux-critic.md

Then read the rubric:
  $RUBRIC

Inputs:
  screenshot_dir: $screenshot_dir
  rubric_path:    $RUBRIC
  output_path:    $out_path
  prior_feedback: $UX_DIR/iter-$(printf '%02d' "$((iter-1))").feedback.json (if exists)
  scope_filter:   all

Workflow per contract:
  1. Read rubric.yaml + 9 noise patterns to ignore.
  2. Read all PNG screenshots under screenshot_dir (use Glob then Read).
     Limit to 1 attempt per E2E flow if cost is a concern (~8 screenshots
     covers all flows).
  3. Score each axis 1-5 with evidence + deductions.
  4. Compute overall = min(round(weighted_mean*2), 9).
  5. Top 5 findings sorted by severity * weight.
  6. Write strict-JSON to $out_path.
  7. Append a 1-line LEARNINGS entry to $LEARNINGS:
       [<ts>] iter=ux-$iter pattern=ux_critic_score detail=overall=<N> axes_below=[...]

Cost ceiling: \$1.50. If approaching, stop and write whatever feedback you have.
Return ONLY a JSON summary at the end:
  {"iter": $iter, "overall": N, "axes_below_target": [...], "cost_usd": X}
EOF

  log "iter=$iter critic dispatch (opus)"
  claude -p --dangerously-skip-permissions --model opus \
    --output-format json \
    < "$prompt_file" > "$out_log" 2>&1
  local rc=$?
  echo "$out_path"
}

decide_done() {
  local feedback="$1"
  if [ ! -f "$feedback" ]; then
    echo "missing"
    return
  fi
  local overall=$(jq -r '.overall // 0' "$feedback")
  local target=$(jq -r '.convergence.target_overall // 8' "$feedback")
  local need_coder=$(jq -r '.convergence.should_dispatch_coder // false' "$feedback")
  if [ "$overall" -ge "$target" ] && [ "$need_coder" = "false" ]; then
    echo "converged"
  else
    echo "continue"
  fi
}

run_coder() {
  local iter="$1"
  local feedback="$2"
  local prompt_file="$UX_DIR/iter-$(printf '%02d' "$iter").coder.prompt"
  local out_log="$UX_DIR/iter-$(printf '%02d' "$iter").coder.log"

  # Pull top finding from feedback
  local top=$(jq -c '.top_findings[0] // {}' "$feedback")
  local check_id="UX-ITER-$(printf '%02d' "$iter")-$(jq -r '.axis // "unknown"' <<<"$top")"

  cat > "$prompt_file" <<EOF
You are operating as **sediment-coder**. Read your contract:
  $REPO_ROOT/.claude/agents/sediment-coder.md

Work-order from ux-critic iter $iter:
$top

Full feedback (read for axis-level context):
  $feedback

Workflow:
  1. cd $REPO_ROOT
  2. bash harness/scripts/ai-commit.sh baseline UX-$iter P2
  3. bash harness/scripts/ai-commit.sh begin UX-$iter
  4. Read each screenshot in the finding's "screenshot" + "files_likely_changed" list.
  5. Apply the suggested_fix exactly. Don't refactor unrelated code.
  6. bash harness/scripts/ai-commit.sh gate UX-$iter P2
     (gate auto-bounces services + runs lint-sql)
  7. If gate passes: bash harness/scripts/ai-commit.sh commit UX-$iter "ux: \$AXIS — iter $iter — \$ONE_LINE"
  8. Append LEARNINGS entry pattern=ux_coder_fix detail=axis=\$AXIS iter=$iter

Hard rules from your contract: never edit guard.json forbidden files; never
merge to main; never amend; cost ceiling \$1.50.

Return ONLY: {"iter": $iter, "status": "ok|reject|skip", "files_changed": [...], "cost_usd": X}
EOF

  log "iter=$iter coder dispatch (sonnet)"
  claude -p --dangerously-skip-permissions --model sonnet \
    --output-format json \
    < "$prompt_file" > "$out_log" 2>&1
  local rc=$?
  echo "$out_log"
}

bump_state() {
  local iter="$1"; local overall="$2"; local cost="$3"
  jq --argjson it "$iter" --argjson ov "$overall" --argjson c "$cost" --arg t "$(ts)" \
    '.iteration = $it
     | .last_run_at = $t
     | .cumulative_cost_usd = ((.cumulative_cost_usd // 0) + $c)
     | .axis_history += [{iter: $it, overall: $ov, cost_usd: $c, ts: $t}]
     | (if $ov > (.best_overall // 0) then .best_overall = $ov | .best_iter = $it else . end)' \
    "$STATE_FILE" > "$STATE_FILE.tmp" && mv "$STATE_FILE.tmp" "$STATE_FILE"
}

# ============================================================
# Main loop
# ============================================================

log "ux-loop starting iter=$ITER max=$MAX_ITER budget=\$$COST_BUDGET duration=${DURATION_S}s"

while :; do
  ITER=$((ITER + 1))

  # Stop conditions
  if [ "$ITER" -gt "$MAX_ITER" ]; then finish "max_iter_reached"; fi
  cur_cost=$(jq -r '.cumulative_cost_usd // 0' "$STATE_FILE")
  if (( $(echo "$cur_cost >= $COST_BUDGET" | bc -l) )); then
    finish "cost_budget_exhausted"
  fi
  if [ "$DURATION_S" -gt 0 ]; then
    elapsed=$(($(date +%s) - START_EPOCH))
    if [ "$elapsed" -ge "$DURATION_S" ]; then
      finish "duration_reached"
    fi
  fi

  log "─── ux-loop iter $ITER ───"

  if [ "$DRY_RUN" = "1" ]; then
    log "  [dry-run] skip capture/critic/coder"
    bump_state "$ITER" 0 0
    sleep 2
    continue
  fi

  # 1. Capture
  SCREEN_DIR=$(run_capture "$ITER" | tail -1)
  if [ -z "$SCREEN_DIR" ] || [ ! -d "$SCREEN_DIR" ]; then
    log "  capture produced no screenshots — sleeping + retrying"
    sleep 30
    continue
  fi

  # 2. Critic
  FB=$(run_critic "$ITER" "$SCREEN_DIR" | tail -1)
  ITER_COST=0
  if [ -f "$UX_DIR/iter-$(printf '%02d' "$ITER").critic.log" ]; then
    ITER_COST=$(jq -r '.total_cost_usd // 0' "$UX_DIR/iter-$(printf '%02d' "$ITER").critic.log" 2>/dev/null || echo 0)
  fi
  OVERALL=0
  [ -f "$FB" ] && OVERALL=$(jq -r '.overall // 0' "$FB")
  log "  iter=$ITER overall=$OVERALL cost=\$$ITER_COST"

  # 3. Decide
  STATE=$(decide_done "$FB")
  log "  state=$STATE"
  if [ "$STATE" = "converged" ]; then
    bump_state "$ITER" "$OVERALL" "$ITER_COST"
    finish "converged"
  fi

  # 4. Fix
  if [ "$STATE" = "continue" ]; then
    CODER_LOG=$(run_coder "$ITER" "$FB" | tail -1)
    CODER_COST=0
    if [ -f "$CODER_LOG" ]; then
      CODER_COST=$(jq -r '.total_cost_usd // 0' "$CODER_LOG" 2>/dev/null || echo 0)
    fi
    ITER_COST=$(echo "$ITER_COST + $CODER_COST" | bc -l)
    log "  coder cost=\$$CODER_COST  total iter cost=\$$ITER_COST"
  fi

  bump_state "$ITER" "$OVERALL" "$ITER_COST"

  sleep 5
done
