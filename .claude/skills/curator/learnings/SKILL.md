---
name: curator:learnings
description: Read or append to LEARNINGS.md. Self-healing memory across Ralph iterations.
user_invocable: true
triggers:
  - "/curator:learnings"
  - "learnings"
---

## Args

```
/curator:learnings              # show recent (last 20 entries)
/curator:learnings --tail N     # show last N entries
/curator:learnings --add "<one-line lesson>"   # append manually
/curator:learnings --grep <pattern>    # search
/curator:learnings --stats             # count by pattern type
```

## Workflow

```bash
LEARN="products/sediment/harness/ralph/LEARNINGS.md"
[ -f "$LEARN" ] || cp products/sediment/harness/ralph/LEARNINGS.template.md "$LEARN" 2>/dev/null
```

For `--show` (default):
```bash
tail -50 "$LEARN"
```

For `--add`:
```bash
{
  echo
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] manual"
  echo "  $1"
} >> "$LEARN"
```

For `--grep PATTERN`:
```bash
grep -B1 -A3 "$PATTERN" "$LEARN"
```

For `--stats`:
```bash
grep -oE "pattern=[a-z_]+" "$LEARN" | sort | uniq -c | sort -rn
```

## Hard rules

- Append-only (never delete entries).
- LEARNINGS.md is the source of truth for "what we learned across 200 iters."
  RALPH_PROMPT.md instructs Ralph to read it every iter.
