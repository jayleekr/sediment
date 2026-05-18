---
name: curator:tune-rag
description: Diagnose RAG retrieval failures and propose chunker / embedding / index tuning. Read-only proposal — does not auto-apply.
user_invocable: true
triggers:
  - "/curator:tune-rag"
  - "rag failing"
  - "recall low"
---

## Workflow

```bash
RPT=$(ls -1t output/validation/loop-P1-*/iter-*/report.json 2>/dev/null | head -1)
[ -z "$RPT" ] && RPT=$(ls -1t output/validation/P1-latest.json 2>/dev/null | head -1)
```

If `RPT` empty: ask user to run `/curator-validate p1` first.

Dispatch:
```
subagent_type: curator-rag-tuner
prompt:
  report_json: <RPT>
  auto_ablate: false
  Diagnose the failed P1-GOLDEN-RAG-* / P1-SEARCH-* checks.
  Return JSON output contract + write tuning-proposal-*.md.
```

Surface: diagnosis 1-line + top 1 proposed change. Full proposal in the .md file.
