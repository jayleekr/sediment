#!/usr/bin/env bash
# run-p3-validator.sh — daily P3 validator + Discord notification on regression.
#
# Designed to be invoked by launchd (com.hypeproof.sediment.p3-validator.plist).
# Idempotent — safe to invoke manually for local test.
#
# Behavior:
#   1. Run `python -m validator --phase P3` from services/sediment
#   2. Compare new score to previous run (last P3 JSON file)
#   3. On regression OR new failures, send Discord DM to Jay via notify-discord.sh
#   4. On full pass (P3 score >= pass threshold), send a brief OK message
#      every 7 runs (silent on routine green days, vocal on first green)
#
# Exit codes:
#   0 — validator ran (regardless of pass/fail). Discord notification fired
#       if needed. Cron should never see non-zero from this script.
#   2 — fatal infrastructure error (postgres down, venv missing). Cron logs
#       the error to its stderr file.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SVC="$REPO_ROOT/services/sediment"
VAL_DIR="$REPO_ROOT/output/validation"
NOTIFY="$HOME/CodeWorkspace/hypeproof/cron-prompts/notify-discord.sh"

# Strip parent Claude Code env if any (consistent with ralph supervisor)
unset CLAUDE_CODE_ENTRYPOINT CLAUDE_CODE_EXECPATH CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS \
      CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS CLAUDE_CODE_MAX_OUTPUT_TOKENS \
      CLAUDE_CODE_SSE_PORT CLAUDECODE 2>/dev/null || true

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

# Pre-flight
if [ ! -x "$SVC/.venv/bin/python" ]; then
  echo "[$(ts)] FATAL: $SVC/.venv missing — run setup-env.sh first" >&2
  exit 2
fi

# Find prior P3 score for comparison BEFORE running
PRIOR_FILE=$(ls -1t "$VAL_DIR"/P3-iter*.json 2>/dev/null | head -1)
PRIOR_SCORE="0"
PRIOR_BLOCKERS_PASSED="0"
PRIOR_BLOCKERS_TOTAL="0"
if [ -n "$PRIOR_FILE" ] && command -v jq >/dev/null 2>&1; then
  PRIOR_SCORE=$(jq -r '.score_pct // 0' "$PRIOR_FILE" 2>/dev/null || echo "0")
  PRIOR_BLOCKERS_PASSED=$(jq -r '.blockers_passed // 0' "$PRIOR_FILE" 2>/dev/null || echo "0")
  PRIOR_BLOCKERS_TOTAL=$(jq -r '.blockers_total // 0' "$PRIOR_FILE" 2>/dev/null || echo "0")
fi

# Run validator
cd "$SVC"
RUN_LOG="$VAL_DIR/p3-cron-$(date +%Y-%m-%d).log"
mkdir -p "$VAL_DIR"
.venv/bin/python -m validator --phase P3 > "$RUN_LOG" 2>&1
RC=$?

# Find new file
NEW_FILE=$(ls -1t "$VAL_DIR"/P3-iter*.json 2>/dev/null | head -1)
if [ -z "$NEW_FILE" ] || [ "$NEW_FILE" = "$PRIOR_FILE" ]; then
  echo "[$(ts)] FATAL: validator did not write a new P3 JSON" >&2
  if [ -x "$NOTIFY" ]; then
    "$NOTIFY" ":warning: Sediment P3 cron failed — validator wrote no result. See $RUN_LOG"
  fi
  exit 0  # don't break cron
fi

NEW_SCORE=$(jq -r '.score_pct' "$NEW_FILE")
NEW_PASSED=$(jq -r '.passed' "$NEW_FILE")
NEW_BLOCKERS_PASSED=$(jq -r '.blockers_passed' "$NEW_FILE")
NEW_BLOCKERS_TOTAL=$(jq -r '.blockers_total' "$NEW_FILE")

# Decide whether to notify
NOTIFY_REASON=""
REGRESS=$(awk -v n="$NEW_SCORE" -v p="$PRIOR_SCORE" 'BEGIN{print (n+0 < p+0 - 0.5) ? 1 : 0}')

if [ "$NEW_PASSED" != "true" ]; then
  NOTIFY_REASON="P3 FAILING"
elif [ "$REGRESS" = "1" ]; then
  NOTIFY_REASON="P3 regression: $PRIOR_SCORE% -> $NEW_SCORE%"
elif [ "$NEW_BLOCKERS_PASSED" -lt "$PRIOR_BLOCKERS_PASSED" ]; then
  NOTIFY_REASON="P3 lost a blocker: $PRIOR_BLOCKERS_PASSED -> $NEW_BLOCKERS_PASSED"
else
  # All good — emit a heartbeat once per week (Mondays)
  if [ "$(date +%u)" = "1" ]; then
    NOTIFY_REASON="weekly_heartbeat"
  fi
fi

if [ -n "$NOTIFY_REASON" ] && [ -x "$NOTIFY" ]; then
  case "$NOTIFY_REASON" in
    weekly_heartbeat)
      MSG=":green_circle: Sediment P3 weekly heartbeat — score ${NEW_SCORE}%, blockers ${NEW_BLOCKERS_PASSED}/${NEW_BLOCKERS_TOTAL}. All green."
      ;;
    *)
      # Build a regression / failure message with top 3 failing checks
      FAIL_LIST=$(jq -r '.results[] | select(.passed == false) | "  - " + .id + " (" + .severity + "): " + (.title // "")' "$NEW_FILE" | head -5 | tr '\n' '\n')
      MSG=":red_circle: Sediment P3 alert: ${NOTIFY_REASON}
  Score: ${NEW_SCORE}% (was ${PRIOR_SCORE}%)
  Blockers: ${NEW_BLOCKERS_PASSED}/${NEW_BLOCKERS_TOTAL} (was ${PRIOR_BLOCKERS_PASSED}/${PRIOR_BLOCKERS_TOTAL})
  Top failures:
${FAIL_LIST}
  Full report: ${NEW_FILE}"
      ;;
  esac
  echo "$MSG" | "$NOTIFY" || echo "[$(ts)] notify-discord failed (silently ignored)" >&2
fi

# Append a one-line summary to a rolling P3 history file for trend analysis
HISTORY="$VAL_DIR/p3-history.log"
echo "[$(ts)] score=${NEW_SCORE} blockers=${NEW_BLOCKERS_PASSED}/${NEW_BLOCKERS_TOTAL} passed=${NEW_PASSED} reason=${NOTIFY_REASON:-none}" >> "$HISTORY"

exit 0
