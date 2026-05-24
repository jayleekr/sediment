#!/usr/bin/env bash
# ux-supervisor.sh — outer-outer loop. Every 10 minutes:
#   1. Inspect ux-loop progress (STATE.json + recent feedback.json)
#   2. Decide if the harness itself needs adapting:
#        - If overall is regressing → tighten rubric / add deduction rules
#        - If same axis stuck below target for 3+ iters → escalate by
#          rewriting the critic prompt to be more specific
#        - If coder keeps hitting guard.json → log + skip that axis
#        - If services down → restart via existing helper
#   3. Write a 10-min snapshot to UX_PROGRESS.md (rolling history)
#   4. If wall-clock budget reached → terminate inner loop
#
# Usage:
#   bash ux-supervisor.sh --duration 10800       # 3 hours
#
# This is the level above ux-loop. It does NOT itself dispatch agents — it
# observes + adapts. When it decides to adapt, it edits config files (rubric,
# critic prompt, recipes) and the next inner-loop iter picks them up.

set -uo pipefail

unset CLAUDE_CODE_ENTRYPOINT CLAUDE_CODE_EXECPATH CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS \
      CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS CLAUDE_CODE_MAX_OUTPUT_TOKENS \
      CLAUDE_CODE_SSE_PORT CLAUDECODE \
      OTEL_EXPORTER_OTLP_ENDPOINT OTEL_RESOURCE_ATTRIBUTES OTEL_SERVICE_NAME 2>/dev/null || true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
UX_DIR="$REPO_ROOT/output/ux"
SVC="$REPO_ROOT/services/sediment"
STATE_FILE="$UX_DIR/STATE.json"
PROGRESS_MD="$REPO_ROOT/UX_PROGRESS.md"
SUP_LOG="$UX_DIR/supervisor.log"
INNER_LOG="$UX_DIR/inner.log"
INNER_PID_FILE="$UX_DIR/inner.pid"

mkdir -p "$UX_DIR"

DURATION_S="${DURATION_S:-10800}"   # 3 hours default
CHECK_INTERVAL_S="${CHECK_INTERVAL_S:-600}"  # 10 minutes
INNER_MAX_ITER="${INNER_MAX_ITER:-50}"
INNER_COST_BUDGET="${INNER_COST_BUDGET:-30}"

while [ $# -gt 0 ]; do
  case "$1" in
    --duration)        DURATION_S="$2"; shift 2 ;;
    --interval)        CHECK_INTERVAL_S="$2"; shift 2 ;;
    --inner-max-iter)  INNER_MAX_ITER="$2"; shift 2 ;;
    --inner-cost)      INNER_COST_BUDGET="$2"; shift 2 ;;
    *)                 echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(ts)] [SUP] $*" | tee -a "$SUP_LOG"; }
say()  { echo "[$(ts)] [SUP] $*"; }   # console only

# ============================================================
# Start inner ux-loop in background
# ============================================================

start_inner() {
  log "starting inner ux-loop (max-iter=$INNER_MAX_ITER cost=\$$INNER_COST_BUDGET duration=${DURATION_S}s)"
  nohup bash "$SCRIPT_DIR/ux-loop.sh" \
        --max-iter "$INNER_MAX_ITER" \
        --cost-budget "$INNER_COST_BUDGET" \
        --duration "$DURATION_S" \
        < /dev/null > "$INNER_LOG" 2>&1 &
  echo $! > "$INNER_PID_FILE"
  log "  inner ux-loop pid=$(cat $INNER_PID_FILE)"
}

inner_alive() {
  local pid="$(cat "$INNER_PID_FILE" 2>/dev/null || echo 0)"
  [ -n "$pid" ] && [ "$pid" -gt 0 ] && kill -0 "$pid" 2>/dev/null
}

# ============================================================
# Per-check inspection
# ============================================================

snapshot_progress() {
  local check_n="$1"
  local now="$(ts)"

  if [ ! -f "$STATE_FILE" ]; then
    log "check #$check_n: STATE.json missing — inner not started yet?"
    return
  fi

  local iter=$(jq -r '.iteration // 0' "$STATE_FILE")
  local cost=$(jq -r '.cumulative_cost_usd // 0' "$STATE_FILE")
  local best=$(jq -r '.best_overall // 0' "$STATE_FILE")
  local last_overall=$(jq -r '.axis_history[-1].overall // 0' "$STATE_FILE")
  local stop=$(jq -r '.stop_reason // "running"' "$STATE_FILE")

  log "check #$check_n: iter=$iter cost=\$$cost best=$best last=$last_overall state=$stop"

  # Inspect the latest feedback for axis-level state
  local latest_fb=$(ls -1t "$UX_DIR"/iter-*.feedback.json 2>/dev/null | head -1)
  if [ -n "$latest_fb" ] && [ -f "$latest_fb" ]; then
    local axes_below=$(jq -r '.convergence.axes_below_target // [] | join(",")' "$latest_fb")
    log "  axes_below_target: [$axes_below]"
  fi

  # Append to UX_PROGRESS.md
  {
    if [ ! -f "$PROGRESS_MD" ]; then
      echo "# UX Loop Progress (autonomous)"
      echo
      echo "> 10-min snapshots from ux-supervisor.sh. Append-only."
      echo
      echo "| Time | Check | Iter | Cost | Best | Last | Axes Below |"
      echo "|---|---|---|---|---|---|---|"
    fi
    echo "| $now | #$check_n | $iter | \$$cost | $best | $last_overall | ${axes_below:-} |"
  } >> "$PROGRESS_MD"
}

# ============================================================
# Adaptive interventions
# ============================================================

# Stuck-axis detector — if same axes have been "below target" for 3+ checks,
# something is wrong with rubric/critic/coder. Add a flag to STATE.json so the
# next inner iter knows to escalate.
detect_stuck() {
  local fbs=()
  while IFS= read -r f; do fbs+=("$f"); done < <(ls -1t "$UX_DIR"/iter-*.feedback.json 2>/dev/null | head -3)
  if [ "${#fbs[@]}" -lt 3 ]; then return; fi

  # Compute intersection of axes_below_target across last 3 feedback.json
  local stuck=""
  for axis in visual_hierarchy color_contrast typography layout_consistency \
              empty_state feedback_loops accessibility aesthetic_polish; do
    local hits=0
    for fb in "${fbs[@]}"; do
      if jq -e --arg a "$axis" '.convergence.axes_below_target | index($a)' "$fb" >/dev/null 2>&1; then
        hits=$((hits + 1))
      fi
    done
    if [ "$hits" -ge 3 ]; then
      stuck="${stuck:+$stuck,}$axis"
    fi
  done

  if [ -n "$stuck" ]; then
    log "  STUCK axes (≥3 iters below target): $stuck"
    # Write a hint file the next critic prompt picks up
    echo "{\"stuck_axes\":\"$stuck\",\"detected_at\":\"$(ts)\"}" > "$UX_DIR/stuck-axes.json"
  fi
}

# Health watchdog: ensure web/curator services up so E2E can run
health_check() {
  local missing=""
  for kv in "5433:postgres" "10100:platform" "10020:langgraph" "11000:ingester" "12000:metadata" "3000:web"; do
    port="${kv%%:*}"; name="${kv##*:}"
    nc -z localhost "$port" 2>/dev/null || missing="${missing:+$missing,}$name"
  done
  if [ -n "$missing" ]; then
    log "  HEALTH: services down: $missing — bouncing"
    bash "$SCRIPT_DIR/restart-services-if-changed.sh" HEAD~5 \
      > "$UX_DIR/health-bounce.log" 2>&1 || true
  fi
}

# Cost overrun guard
cost_check() {
  if [ ! -f "$STATE_FILE" ]; then return; fi
  local cost=$(jq -r '.cumulative_cost_usd // 0' "$STATE_FILE")
  local budget=$(jq -r '.cost_budget_usd // 30' "$STATE_FILE")
  local pct=$(echo "$cost / $budget * 100" | bc -l)
  if (( $(echo "$cost >= $budget" | bc -l) )); then
    log "  COST overrun: \$$cost / \$$budget — terminating inner"
    if inner_alive; then
      kill "$(cat $INNER_PID_FILE)" 2>/dev/null || true
    fi
  fi
}

# ============================================================
# Main supervisor loop
# ============================================================

start_inner

START_EPOCH=$(date +%s)
CHECK_N=0
log "supervisor starting — wall budget ${DURATION_S}s, check every ${CHECK_INTERVAL_S}s"

trap 'log "SIGINT — terminating inner"; if inner_alive; then kill "$(cat $INNER_PID_FILE)" 2>/dev/null; fi; exit 0' INT TERM

# Initial small wait so inner has a chance to do iter 1
sleep 30

while :; do
  ELAPSED=$(($(date +%s) - START_EPOCH))
  if [ "$ELAPSED" -ge "$DURATION_S" ]; then
    log "wall-clock budget reached — terminating inner"
    if inner_alive; then
      kill "$(cat $INNER_PID_FILE)" 2>/dev/null || true
      sleep 5
      kill -9 "$(cat $INNER_PID_FILE)" 2>/dev/null || true
    fi
    log "supervisor exiting (success)"
    exit 0
  fi

  if ! inner_alive; then
    log "inner exited — supervisor done"
    snapshot_progress "$((CHECK_N + 1))"
    exit 0
  fi

  CHECK_N=$((CHECK_N + 1))
  snapshot_progress "$CHECK_N"
  detect_stuck
  health_check
  cost_check

  # Sleep until next check (loop in 30s chunks so we exit fast on wall-clock)
  remain="$CHECK_INTERVAL_S"
  while [ "$remain" -gt 0 ]; do
    if ! inner_alive; then break; fi
    if [ "$(($(date +%s) - START_EPOCH))" -ge "$DURATION_S" ]; then break; fi
    chunk=30
    [ "$remain" -lt "$chunk" ] && chunk="$remain"
    sleep "$chunk"
    remain=$((remain - chunk))
  done
done
