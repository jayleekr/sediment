---
name: curator:compact
description: Compact JOURNAL.md when it grows beyond threshold. Moves old entries to archive, generates summary.
user_invocable: true
triggers:
  - "/curator:compact"
  - "journal too big"
  - "compact ralph"
---

## Purpose

Context-window hygiene. Long-running Ralph accumulates JOURNAL entries; if Ralph
loads the full file each iter, context cost grows linearly. Compact periodically.

## Threshold

- `JOURNAL.md > 5000 lines` OR `> 200KB` → compact
- Default: keep last 1000 lines + reference to JOURNAL.compacted.md

## Workflow

```bash
JOURNAL="products/sediment/harness/ralph/JOURNAL.md"
ARCHIVE="products/sediment/harness/ralph/JOURNAL.archive.md"
COMPACTED="products/sediment/harness/ralph/JOURNAL.compacted.md"

LINES=$(wc -l < "$JOURNAL")
if [ "$LINES" -lt 5000 ]; then
  echo "no compaction needed (lines=$LINES)"
  exit 0
fi

# Move oldest 80% to archive (append)
HEAD=$((LINES * 80 / 100))
head -$HEAD "$JOURNAL" >> "$ARCHIVE"
tail -$((LINES - HEAD)) "$JOURNAL" > "$JOURNAL.tmp" && mv "$JOURNAL.tmp" "$JOURNAL"
```

Then summarize the new archive lines into COMPACTED.md (append-only):
- read newly-archived lines
- LLM-summarize: top 3 patterns, phase progression, errors that recurred
- append to JOURNAL.compacted.md with timestamp

LLM call uses Sonnet, max 500 output tokens, focused prompt:
```
Summarize these N journal lines into:
- 1 line: phase progression (P0 → P1 → ...)
- 3 bullet points: most common patterns
- 1 bullet: any unresolved error trend
Total ≤ 200 words.
```

## Hard rules

- Never delete from JOURNAL.archive.md. It's the full history.
- If COMPACTED.md grows above 1MB, compact IT recursively (rare).
- Update RALPH_PROMPT.md instructions: read JOURNAL.compacted.md HEAD + last 50
  lines of JOURNAL.md (not full file).
