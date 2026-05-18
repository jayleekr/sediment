#!/usr/bin/env bash
# Live monitor for Ralph loop + inner validator runs.
# Run in a separate terminal alongside ralph.sh.
#
# Shows:
#   ┌─ Ralph status ─────────────────────────────┐  ┌─ TODO ─┐
#   │ iter, phase, last_action, cost            │  │ open N │
#   └────────────────────────────────────────────┘  └────────┘
#   Recent journal:
#   ...
#   Latest validator score:
#   ...
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
RALPH_DIR="$REPO_ROOT/products/sediment/harness/ralph"
VALIDATION_DIR="$REPO_ROOT/output/validation"

INTERVAL="${INTERVAL:-3}"

clear
while :; do
  printf '\033[H\033[2J'  # clear
  echo "═══ AI Curator — Ralph Monitor ═══════════════════════════════════════════"
  echo "  $(date '+%Y-%m-%d %H:%M:%S')   refresh ${INTERVAL}s   Ctrl-C to exit"
  echo

  if [ -f "$RALPH_DIR/STATE.json" ]; then
    iter=$(jq -r '.iteration // 0' "$RALPH_DIR/STATE.json")
    phase=$(jq -r '.current_phase // "-"' "$RALPH_DIR/STATE.json")
    cost=$(jq -r '.cumulative_cost_usd // 0' "$RALPH_DIR/STATE.json")
    budget=$(jq -r '.cost_budget_usd // 100' "$RALPH_DIR/STATE.json")
    max_iter=$(jq -r '.max_iterations // 200' "$RALPH_DIR/STATE.json")
    stop_reason=$(jq -r '.stop_reason // "running"' "$RALPH_DIR/STATE.json")
    last_action=$(jq -r '.last_action // "-"' "$RALPH_DIR/STATE.json")
    last_rc=$(jq -r '.last_exit_code // "-"' "$RALPH_DIR/STATE.json")
    open=$(grep -c '^- \[ \]' "$RALPH_DIR/TODO.md" 2>/dev/null || echo 0)
    done=$(grep -c '^- \[x\]' "$RALPH_DIR/TODO.md" 2>/dev/null || echo 0)
    echo "RALPH"
    echo "  iter:        $iter / $max_iter"
    echo "  phase:       $phase"
    echo "  status:      $stop_reason"
    echo "  cost:        \$$cost / \$$budget"
    echo "  last action: $last_action  (rc=$last_rc)"
    echo "  TODO:        $open open · $done done"
  else
    echo "RALPH  (not started — run ./ralph.sh)"
  fi

  echo
  echo "─── Journal (last 10 lines) ─────────────────────────────────────────────"
  if [ -f "$RALPH_DIR/JOURNAL.md" ]; then
    tail -10 "$RALPH_DIR/JOURNAL.md" | sed 's/^/  /'
  else
    echo "  (no journal yet)"
  fi

  echo
  echo "─── Latest validation report ────────────────────────────────────────────"
  latest_json=$(ls -1t "$VALIDATION_DIR"/*-latest.json 2>/dev/null | head -1)
  if [ -n "$latest_json" ]; then
    phase_name=$(jq -r '.phase_id' "$latest_json" 2>/dev/null || echo "?")
    pct=$(jq -r '.score_pct' "$latest_json" 2>/dev/null || echo "?")
    blk=$(jq -r '"\(.blockers_passed)/\(.blockers_total)"' "$latest_json" 2>/dev/null || echo "?")
    passed=$(jq -r '.passed' "$latest_json" 2>/dev/null || echo "?")
    echo "  $phase_name  score=$pct%  blockers=$blk  passed=$passed"
    echo "  source: $latest_json"
  else
    echo "  (no validation reports yet)"
  fi

  echo
  echo "─── Active loops (history.csv tail) ─────────────────────────────────────"
  for hist in "$VALIDATION_DIR"/loop-*-*/history.csv; do
    [ -f "$hist" ] || continue
    last_dir=$(dirname "$hist")
    last_phase=$(basename "$last_dir" | cut -d- -f2)
    tail -3 "$hist" | awk -F, -v p="$last_phase" 'NR>1 {printf "  %s iter=%s pct=%s%% blk=%s\n", p, $1, $5, $6"/"$7}'
  done | head -10

  echo
  echo "─── Service health ──────────────────────────────────────────────────────"
  for port_label in "5433:postgres" "6380:redis" "10100:platform" "10020:langgraph" "11000:ingester" "12000:metadata" "3000:web"; do
    port="${port_label%%:*}"
    label="${port_label##*:}"
    if nc -z localhost "$port" 2>/dev/null; then
      printf "  %-10s :%s ✓\n" "$label" "$port"
    else
      printf "  %-10s :%s ✗\n" "$label" "$port"
    fi
  done

  sleep "$INTERVAL"
done
