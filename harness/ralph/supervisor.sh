#!/usr/bin/env bash
# Ralph Supervisor — restart Ralph if it dies, with cooldown + max retries.
#
# Usage:
#   ./supervisor.sh                     # default: max 10 restarts, 60s cooldown
#   ./supervisor.sh --max-restarts 20   # tighter / looser as needed
#   ./supervisor.sh --cooldown 300      # 5min between restarts (rate-limit friendly)
#
# This is the OUTER-OUTER loop:
#   supervisor.sh  → ralph.sh (200 iter)  → claude -p (per iter)
#
# When Ralph exits naturally (converged / stalled / cost), supervisor stops.
# When Ralph crashes (sigterm / oom / unexpected exit), supervisor restarts.
# When 5 consecutive crashes within 30 min: supervisor gives up + writes
# CRASH_REPORT.md.
set -uo pipefail

# Strip parent Claude Code session env (LEARNINGS 2026-05-08
# pattern=parent_session_env_inheritance_breaks_child). Child claude -p
# subprocesses must not inherit our session's CLAUDECODE / CLAUDE_CODE_* /
# OTEL_* vars or they'll be detected as nested sessions and refuse to start.
# Caller no longer needs to wrap with `env -u ...`.
unset CLAUDE_CODE_ENTRYPOINT CLAUDE_CODE_EXECPATH CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS \
      CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS CLAUDE_CODE_MAX_OUTPUT_TOKENS \
      CLAUDE_CODE_SSE_PORT CLAUDECODE \
      OTEL_EXPORTER_OTLP_ENDPOINT OTEL_RESOURCE_ATTRIBUTES OTEL_SERVICE_NAME 2>/dev/null || true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
RALPH="$SCRIPT_DIR/ralph.sh"
LOG_DIR="$REPO_ROOT/output/ralph"
mkdir -p "$LOG_DIR"

MAX_RESTARTS="${MAX_RESTARTS:-10}"
COOLDOWN="${COOLDOWN:-60}"
CRASH_WINDOW="${CRASH_WINDOW:-1800}"  # 30 min
CRASH_LIMIT="${CRASH_LIMIT:-5}"       # consecutive crashes within window

while [ $# -gt 0 ]; do
  case "$1" in
    --max-restarts) MAX_RESTARTS="$2"; shift 2 ;;
    --cooldown)     COOLDOWN="$2"; shift 2 ;;
    *)              break ;;
  esac
done

restart_count=0
crash_times=()  # epoch seconds of recent crashes
SUPERVISOR_LOG="$LOG_DIR/supervisor.log"

log() {
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$SUPERVISOR_LOG"
}

cleanup_old_crashes() {
  local now=$(date +%s)
  local cutoff=$((now - CRASH_WINDOW))
  local kept=()
  for t in "${crash_times[@]:-}"; do
    [ -z "$t" ] && continue
    [ "$t" -gt "$cutoff" ] && kept+=("$t")
  done
  crash_times=("${kept[@]}")
}

trap 'log "supervisor SIGINT — passing to Ralph"; pkill -TERM -P $$ 2>/dev/null; exit 0' INT TERM

log "supervisor starting. max_restarts=$MAX_RESTARTS cooldown=${COOLDOWN}s"

while [ "$restart_count" -lt "$MAX_RESTARTS" ]; do
  start_epoch=$(date +%s)
  log "starting ralph (run #$((restart_count + 1)))"

  set +e
  bash "$RALPH" "$@"
  rc=$?
  set -e

  end_epoch=$(date +%s)
  duration=$((end_epoch - start_epoch))
  log "ralph exited rc=$rc duration=${duration}s"

  # Read stop_reason from STATE.json
  stop_reason=""
  state_file="$SCRIPT_DIR/STATE.json"
  if [ -f "$state_file" ]; then
    stop_reason=$(jq -r '.stop_reason // ""' "$state_file" 2>/dev/null || echo "")
  fi

  case "$stop_reason" in
    converged|all_todos_done|stop_signal_in_journal)
      log "ralph completed naturally: $stop_reason"
      log "supervisor exiting (success)"
      exit 0
      ;;
    cost_budget_exhausted)
      log "ralph hit cost budget. supervisor exiting (no auto-restart for budget)"
      exit 0
      ;;
    max_iter_reached|stalled_5_iter)
      log "ralph terminated: $stop_reason. supervisor exiting (manual review needed)"
      exit 2
      ;;
    *)
      # Unexpected exit — likely crash
      crash_times+=("$end_epoch")
      cleanup_old_crashes
      n_recent="${#crash_times[@]}"
      log "unexpected exit (recent crashes in ${CRASH_WINDOW}s window: $n_recent)"

      if [ "$n_recent" -ge "$CRASH_LIMIT" ]; then
        log "CRASH_LIMIT reached — writing CRASH_REPORT.md and exiting"
        {
          echo "# Ralph Crash Report"
          echo
          echo "Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
          echo
          echo "Crashed $n_recent times in ${CRASH_WINDOW}s window. Supervisor giving up."
          echo
          echo "## Last STATE.json"
          [ -f "$state_file" ] && cat "$state_file"
          echo
          echo "## Last 50 lines of supervisor log"
          tail -50 "$SUPERVISOR_LOG"
          echo
          echo "## Last iter log"
          last_iter=$(ls -1t "$LOG_DIR"/iter-*.log 2>/dev/null | head -1)
          if [ -n "$last_iter" ]; then
            echo "### $last_iter (tail 50)"
            tail -50 "$last_iter"
          fi
          echo
          echo "## Recovery suggestions"
          echo "1. Check service health: \`./products/sediment/harness/monitor/watch.sh\`"
          echo "2. Run /curator:medic for diagnosis"
          echo "3. Check ANTHROPIC_API_KEY validity"
          echo "4. Resume after fixing: \`./supervisor.sh --max-restarts 5\`"
        } > "$LOG_DIR/CRASH_REPORT.md"
        exit 3
      fi
      ;;
  esac

  restart_count=$((restart_count + 1))
  log "cooldown ${COOLDOWN}s before restart"
  sleep "$COOLDOWN"
done

log "MAX_RESTARTS reached. supervisor exiting."
exit 1
