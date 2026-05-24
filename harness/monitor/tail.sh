#!/usr/bin/env bash
# Tail Ralph journal + most recent iter log.
# Less detailed than watch.sh — just streaming.
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RALPH_DIR="$REPO_ROOT/harness/ralph"
LOG_DIR="$REPO_ROOT/output/ralph"

if [ ! -f "$RALPH_DIR/JOURNAL.md" ]; then
  echo "Ralph not started yet."
  exit 1
fi

tail -F "$RALPH_DIR/JOURNAL.md" &
JOURNAL_PID=$!

trap 'kill $JOURNAL_PID 2>/dev/null' INT TERM EXIT

# Also tail latest iter log
while :; do
  latest=$(ls -1t "$LOG_DIR"/iter-*.log 2>/dev/null | head -1)
  if [ -n "$latest" ] && [ "$latest" != "${PREV_LATEST:-}" ]; then
    echo
    echo "═══ $latest ═══"
    tail -F "$latest" 2>/dev/null &
    TAIL_PID=$!
    PREV_LATEST="$latest"
    sleep 5
    kill "$TAIL_PID" 2>/dev/null || true
  fi
  sleep 2
done
