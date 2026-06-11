---
name: sediment:status
description: Print 1-page status of Ralph + validator + services. Read-only, fast (< 5s).
user_invocable: true
triggers:
  - "/sediment:status"
  - "sediment status"
  - "where are we"
---

## Purpose

Single-screen snapshot. No subagent dispatch, no compute — just read state files and ports.

## Workflow

```bash
# 1. Ralph state
[ -f products/sediment/harness/ralph/STATE.json ] && \
  jq -r '"iter=\(.iteration)/\(.max_iterations) phase=\(.current_phase // "-") status=\(.stop_reason // "running") cost=$\(.cumulative_cost_usd)/\(.cost_budget_usd)"' \
    products/sediment/harness/ralph/STATE.json

# 2. TODO progress
echo -n "TODO: "
grep -c '^- \[ \]' products/sediment/harness/ralph/TODO.md 2>/dev/null
echo -n " open / "
grep -c '^- \[x\]' products/sediment/harness/ralph/TODO.md 2>/dev/null
echo " done"

# 3. Latest journal (last 5)
tail -5 products/sediment/harness/ralph/JOURNAL.md 2>/dev/null

# 4. Latest validation per phase
for ph in P0 P1 P2 P3; do
  f=output/validation/$ph-latest.json
  [ -f "$f" ] && jq -r --arg p $ph '"\($p): score=\(.score_pct)% blockers=\(.blockers_passed)/\(.blockers_total) passed=\(.passed)"' "$f"
done

# 5. Services
for kv in "5433:postgres" "6380:redis" "10100:platform" "10020:langgraph" "11000:ingester" "12000:metadata" "3000:web"; do
  port="${kv%%:*}"; name="${kv##*:}"
  nc -z localhost "$port" 2>/dev/null && echo "  $name :$port ✓" || echo "  $name :$port ✗"
done

# 6. Recent learnings (last 3)
[ -f products/sediment/harness/ralph/LEARNINGS.md ] && \
  echo "--- Learnings (last 3) ---" && \
  grep -A3 "^\[" products/sediment/harness/ralph/LEARNINGS.md | tail -12
```

## Output format

Compact 1-screen output. No headers, no decoration. Pipe-ready.

## Hard rules

- READ-ONLY. Never modify state files.
- Don't fetch validation reports' full content — just header line.
- Cap output at 40 lines.
