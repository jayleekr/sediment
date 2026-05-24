#!/usr/bin/env bash
# feature-supervisor.sh — restart feature-loop.sh on crash with cooldown.
#
# Exit conditions:
#  - feature-loop exits cleanly with stop_reason in STATE.json (target_reached / max_iters / wall_time)
#  - 5 consecutive crashes within 30 min → give up
#  - SIGTERM from outside
set -uo pipefail

unset CLAUDE_CODE_ENTRYPOINT CLAUDE_CODE_EXECPATH CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS \
      CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS CLAUDE_CODE_MAX_OUTPUT_TOKENS \
      CLAUDE_CODE_SSE_PORT CLAUDECODE \
      OTEL_EXPORTER_OTLP_ENDPOINT OTEL_RESOURCE_ATTRIBUTES OTEL_SERVICE_NAME 2>/dev/null || true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Post-rename: PROJECT_DIR = REPO_ROOT = repo top.
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROJECT_DIR="$REPO_ROOT"
LOOP="$SCRIPT_DIR/feature-loop.sh"
LOG_DIR="$REPO_ROOT/output/feature-loop"
SUP_LOG="$LOG_DIR/supervisor.log"
mkdir -p "$LOG_DIR"

MAX_RESTARTS="${MAX_RESTARTS:-10}"
COOLDOWN="${COOLDOWN:-60}"
CRASH_WINDOW="${CRASH_WINDOW:-1800}"
CRASH_LIMIT="${CRASH_LIMIT:-5}"

log() { printf "[%s] supervisor: %s\n" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$SUP_LOG"; }

restart_count=0
crash_times=()
START=$(date +%s)

while [ "$restart_count" -lt "$MAX_RESTARTS" ]; do
  ITER_LOG="$LOG_DIR/run-$(date +%s).log"
  log "starting feature-loop (restart=$restart_count, log=$ITER_LOG)"
  set +e
  bash "$LOOP" > "$ITER_LOG" 2>&1
  rc=$?
  set -e

  STOP_REASON=$(python3 -c "import json,sys
try:
    d=json.load(open('$LOG_DIR/STATE.json'))
    print(d.get('stop_reason') or '')
except: print('')" 2>/dev/null)

  log "feature-loop exited rc=$rc stop_reason='$STOP_REASON'"

  if [ "$rc" -eq 0 ] && [ -n "$STOP_REASON" ]; then
    log "clean exit — STOP"
    break
  fi

  # crash
  now=$(date +%s)
  crash_times+=("$now")
  # prune outside window
  fresh=()
  for t in "${crash_times[@]}"; do
    if [ $((now - t)) -lt "$CRASH_WINDOW" ]; then
      fresh+=("$t")
    fi
  done
  crash_times=("${fresh[@]}")
  if [ "${#crash_times[@]}" -ge "$CRASH_LIMIT" ]; then
    log "$CRASH_LIMIT crashes within ${CRASH_WINDOW}s — GIVING UP"
    {
      echo "# Feature-loop crash report"
      echo "- crashes: ${#crash_times[@]} in ${CRASH_WINDOW}s window"
      echo "- last rc: $rc"
      echo "- last log: $ITER_LOG"
      echo "- last STATE: $LOG_DIR/STATE.json"
    } > "$LOG_DIR/CRASH_REPORT.md"
    exit 1
  fi

  restart_count=$((restart_count+1))
  log "cooldown ${COOLDOWN}s before restart..."
  sleep "$COOLDOWN"
done

log "supervisor done (restarts=$restart_count)"
