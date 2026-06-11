#!/usr/bin/env bash
# Ralph Wiggum Loop for Sediment
#
# Pattern: Huntley 2026 (https://ghuntley.com/ralph/)
# Each iteration spawns a fresh `claude -p --dangerously-skip-permissions` subprocess
# that reads RALPH_PROMPT.md + state files, performs ONE concrete change, and exits.
# This wrapper is the dumb while-loop on the outside.
#
# Usage:
#   ./ralph.sh                  # default: 200 iter cap, $100 cost budget
#   ./ralph.sh --max-iter 50    # tighter cap
#   ./ralph.sh --dry-run        # print what it would do, don't invoke claude
#   ./ralph.sh --resume         # don't reset state, continue from STATE.json
#
# Stop signals:
#   1. STOP appears in JOURNAL.md
#   2. all TODO.md checkboxes checked
#   3. iteration >= max_iterations
#   4. cumulative_cost_usd >= cost_budget_usd
#   5. stalled_count >= 5
#   6. SIGTERM/SIGINT (Ctrl-C)
set -euo pipefail

# Strip parent Claude Code session env (LEARNINGS 2026-05-08
# pattern=parent_session_env_inheritance_breaks_child). Idempotent — supervisor
# already does this, but ralph.sh may also be invoked directly without the
# supervisor wrapper. Child claude -p subprocess must not inherit nested-session
# markers.
unset CLAUDE_CODE_ENTRYPOINT CLAUDE_CODE_EXECPATH CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS \
      CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS CLAUDE_CODE_MAX_OUTPUT_TOKENS \
      CLAUDE_CODE_SSE_PORT CLAUDECODE \
      OTEL_EXPORTER_OTLP_ENDPOINT OTEL_RESOURCE_ATTRIBUTES OTEL_SERVICE_NAME 2>/dev/null || true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RALPH_DIR="$SCRIPT_DIR"
LOG_DIR="$REPO_ROOT/output/ralph"
mkdir -p "$LOG_DIR"

PROMPT_FILE="$RALPH_DIR/RALPH_PROMPT.md"
TODO_FILE="$RALPH_DIR/TODO.md"
JOURNAL_FILE="$RALPH_DIR/JOURNAL.md"
STATE_FILE="$RALPH_DIR/STATE.json"

# Default args
MAX_ITER="${MAX_ITER:-200}"
COST_BUDGET="${COST_BUDGET:-100}"
DRY_RUN=0
RESUME=0
SLEEP_SECONDS="${SLEEP_SECONDS:-2}"
MODEL="${MODEL:-sonnet}"

while [ $# -gt 0 ]; do
  case "$1" in
    --max-iter)   MAX_ITER="$2"; shift 2 ;;
    --cost-budget) COST_BUDGET="$2"; shift 2 ;;
    --dry-run)    DRY_RUN=1; shift ;;
    --resume)     RESUME=1; shift ;;
    --sleep)      SLEEP_SECONDS="$2"; shift 2 ;;
    --model)      MODEL="$2"; shift 2 ;;
    *)            echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

# Initialize state files if not resuming
if [ "$RESUME" = "0" ]; then
  [ -f "$TODO_FILE" ] || cp "$RALPH_DIR/TODO.template.md" "$TODO_FILE"
  [ -f "$JOURNAL_FILE" ] || cp "$RALPH_DIR/JOURNAL.template.md" "$JOURNAL_FILE"
  if [ ! -f "$STATE_FILE" ]; then
    cp "$RALPH_DIR/STATE.template.json" "$STATE_FILE"
    jq --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
       '.started_at = $ts | .max_iterations = '"$MAX_ITER"' | .cost_budget_usd = '"$COST_BUDGET" \
       "$STATE_FILE" > "$STATE_FILE.tmp" && mv "$STATE_FILE.tmp" "$STATE_FILE"
  fi
fi

trap 'finish "interrupted"' INT TERM

finish() {
  local reason="${1:-stopped}"
  jq --arg r "$reason" --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '.stop_reason = $r | .stopped_at = $ts' \
    "$STATE_FILE" > "$STATE_FILE.tmp" && mv "$STATE_FILE.tmp" "$STATE_FILE"
  echo
  echo "=== Ralph stopped: $reason ==="
  echo "  iter      = $(jq -r '.iteration' "$STATE_FILE")"
  echo "  cost      = \$$(jq -r '.cumulative_cost_usd' "$STATE_FILE")"
  # awk always exits 0 — avoids set -e killing the finish path on grep rc=1
  echo "  todo open = $(awk '/^- \[ \]/{c++} END{print c+0}' "$TODO_FILE" 2>/dev/null || echo 0)"
  echo "  journal   = $JOURNAL_FILE"
  exit 0
}

# State integrity guard — fail loud if init didn't create files. Without this,
# `grep -c ... || echo 0` silently treats missing-file as 0 open todos and the
# wrapper triggers a false `all_todos_done` (LEARNINGS 2026-05-08
# pattern=ralph_premature_all_todos_done).
require_state_files() {
  local where="$1"
  for f in "$TODO_FILE" "$JOURNAL_FILE" "$STATE_FILE"; do
    if [ ! -f "$f" ]; then
      echo "[ralph.sh:$where] STATE FILE MISSING: $f" >&2
      finish "state_file_missing"
    fi
  done
}

require_state_files "pre-loop"

iter=0
stalled=0
# Use awk (always rc=0) instead of grep -c (rc=1 when zero matches kills set -e).
# The state-file-missing case is handled by require_state_files above.
prev_open_count=$(awk '/^- \[ \]/{c++} END{print c+0}' "$TODO_FILE")

while :; do
  iter=$((iter + 1))

  # Stop conditions
  if [ "$iter" -gt "$MAX_ITER" ]; then finish "max_iter_reached"; fi

  # Re-check on every iter — the agent in the previous iteration may have
  # deleted or wiped a state file. Better to halt loud than to silently treat
  # missing-file as "all done".
  require_state_files "iter-$iter-top"

  cur_cost=$(jq -r '.cumulative_cost_usd' "$STATE_FILE")
  if (( $(echo "$cur_cost >= $COST_BUDGET" | bc -l) )); then
    finish "cost_budget_exhausted"
  fi

  if grep -q "^STOP" "$JOURNAL_FILE" || \
     tail -5 "$JOURNAL_FILE" | grep -q "STOP"; then
    finish "stop_signal_in_journal"
  fi

  # awk-based count makes "all done" reachable under set -e (LEARNINGS:
  # grep -c returns rc=1 with stdout=0 when zero matches → set -e killed
  # the loop before the all_todos_done branch ever ran).
  open_count=$(awk '/^- \[ \]/{c++} END{print c+0}' "$TODO_FILE")
  if [ "$open_count" = "0" ]; then finish "all_todos_done"; fi

  if [ "$open_count" = "$prev_open_count" ]; then
    stalled=$((stalled + 1))
  else
    stalled=0
  fi
  prev_open_count="$open_count"
  if [ "$stalled" -ge 5 ]; then finish "stalled_5_iter"; fi

  echo "─── ralph iter $iter (todo open: $open_count, stalled: $stalled) ───"

  if [ "$DRY_RUN" = "1" ]; then
    echo "[dry-run] would invoke: claude -p --dangerously-skip-permissions --model $MODEL"
    echo "[dry-run] reading: $PROMPT_FILE"
    sleep 1
    continue
  fi

  # Build the iteration prompt — RALPH_PROMPT.md + key state
  ITER_LOG="$LOG_DIR/iter-$(printf '%04d' "$iter").log"
  ITER_PROMPT="$LOG_DIR/iter-$(printf '%04d' "$iter").prompt"

  # Snapshot state files before invoking the agent. If the agent deletes/wipes
  # a file (LEARNINGS 2026-05-08 pattern=ralph_premature_all_todos_done), the
  # post-iter restore step will recover from these. Backups stay alongside the
  # iter log so we can correlate damage to a specific iteration after the fact.
  ITER_BAK_PREFIX="$LOG_DIR/iter-$(printf '%04d' "$iter")"
  cp "$TODO_FILE"    "$ITER_BAK_PREFIX.TODO.bak"
  cp "$JOURNAL_FILE" "$ITER_BAK_PREFIX.JOURNAL.bak"
  cp "$STATE_FILE"   "$ITER_BAK_PREFIX.STATE.bak"

  {
    cat "$PROMPT_FILE"
    echo
    echo "## Current state (iteration $iter)"
    echo
    echo "### STATE.json"
    cat "$STATE_FILE"
    echo
    echo "### TODO.md (open items)"
    grep '^- \[ \]' "$TODO_FILE" || echo "(none)"
    echo
    echo "### JOURNAL.md (last 20 lines)"
    tail -20 "$JOURNAL_FILE"
  } > "$ITER_PROMPT"

  # Invoke claude headless. --dangerously-skip-permissions inside subprocess.
  # --output-format json gives us a final result object containing
  # total_cost_usd + token usage so cumulative_cost_usd works on MAX too
  # (Anthropic SDK reports the cost it WOULD bill, even when MAX absorbs it).
  # Auto-retry with exponential backoff on rate-limit / network errors.
  rc=99
  attempt=0
  max_attempts=5
  backoff=30
  ITER_JSON="$LOG_DIR/iter-$(printf '%04d' "$iter").json"
  while [ $attempt -lt $max_attempts ]; do
    attempt=$((attempt + 1))
    set +e
    claude -p --dangerously-skip-permissions --model "$MODEL" \
      --output-format json \
      < "$ITER_PROMPT" \
      > "$ITER_JSON" 2>&1
    rc=$?
    set -e

    # Render a human-readable iter log alongside the JSON for grep/tail.
    if [ "$rc" -eq 0 ] && jq -e '.result' "$ITER_JSON" >/dev/null 2>&1; then
      jq -r '.result' "$ITER_JSON" > "$ITER_LOG" 2>/dev/null
    else
      cp "$ITER_JSON" "$ITER_LOG"
    fi

    # Detect rate limit / transient errors in output (claude CLI exit codes vary)
    if [ "$rc" -eq 0 ]; then
      break
    fi
    if grep -qiE "rate.?limit|429|temporarily limiting|server.*overload|timeout|connection" "$ITER_LOG"; then
      sleep_for=$((backoff * attempt))
      echo "  rate-limited or transient error — backoff ${sleep_for}s (attempt $attempt/$max_attempts)"
      jq --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
         --arg msg "rate_limit_backoff_${sleep_for}s" \
         '.last_action = $msg | .last_backoff_at = $ts' \
         "$STATE_FILE" > "$STATE_FILE.tmp" && mv "$STATE_FILE.tmp" "$STATE_FILE"
      sleep "$sleep_for"
      continue
    fi
    # Non-transient error — break and let stall logic decide
    break
  done

  # Post-iter state file recovery — if the agent deleted any state file, restore
  # from the snapshot taken before the invocation. We log the restore so the
  # next iter's prompt sees evidence of the corruption.
  restored=()
  for pair in "$TODO_FILE:$ITER_BAK_PREFIX.TODO.bak" \
              "$JOURNAL_FILE:$ITER_BAK_PREFIX.JOURNAL.bak" \
              "$STATE_FILE:$ITER_BAK_PREFIX.STATE.bak"; do
    live="${pair%%:*}"; bak="${pair##*:}"
    if [ ! -f "$live" ] && [ -f "$bak" ]; then
      cp "$bak" "$live"
      restored+=("$(basename "$live")")
    fi
  done
  if [ "${#restored[@]:-0}" -gt 0 ]; then
    echo "  RESTORED state files (agent deleted): ${restored[*]}" >&2
    {
      echo
      echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] iter=$iter pattern=state_file_restored detail=files=${restored[*]}"
      echo "  cause: iteration agent deleted/wiped state file. Auto-recovered from $ITER_BAK_PREFIX.<NAME>.bak"
      echo "  fix: review $ITER_LOG for the offending command. Reinforce AGENT-INVARIANT in RALPH_PROMPT.md."
    } >> "$RALPH_DIR/LEARNINGS.md"
  fi

  # Extract per-iter cost from the JSON result block (LEARNINGS 2026-05-08
  # side-finding inside ralph_premature_all_todos_done — claude CLI reports
  # total_cost_usd even on MAX subscription).
  ITER_COST=0
  if jq -e '.total_cost_usd' "$ITER_JSON" >/dev/null 2>&1; then
    ITER_COST=$(jq -r '.total_cost_usd // 0' "$ITER_JSON")
  fi

  # Update state — iteration count, cost accumulator, last status.
  jq --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
     --argjson it "$iter" \
     --argjson rc "$rc" \
     --argjson att "$attempt" \
     --argjson icost "$ITER_COST" \
    '.iteration = $it | .last_run_at = $ts | .last_exit_code = $rc | .last_attempts = $att | .last_iter_cost_usd = $icost | .cumulative_cost_usd = ((.cumulative_cost_usd // 0) + $icost)' \
    "$STATE_FILE" > "$STATE_FILE.tmp" && mv "$STATE_FILE.tmp" "$STATE_FILE"

  echo "  exit=$rc log=$ITER_LOG"

  # Tail line of the iter log → console (so monitor sees progress)
  tail -1 "$ITER_LOG" 2>/dev/null || true

  sleep "$SLEEP_SECONDS"
done
