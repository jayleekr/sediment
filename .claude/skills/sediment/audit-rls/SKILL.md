---
name: sediment:audit-rls
description: Read-only RLS audit. Run when any *-RLS-* check fails. Identifies whether leak source is policy/code/pool/test.
user_invocable: true
triggers:
  - "/sediment:audit-rls"
  - "rls leak"
  - "tenant leak"
---

## Workflow

```bash
RPT=$(ls -1t output/validation/*-latest.json 2>/dev/null | head -1)
```

Dispatch:
```
subagent_type: sediment-rls-auditor
prompt:
  report_json: <RPT>
  Run the 4-step audit (policies → roles → propagation → pool).
  Return JSON contract.
```

Surface output. If `release_block: true`, mark message with 🚨 and suggest:
1. `/sediment:fix` to re-apply init.sql if policy-level
2. `/sediment:propose-rubric` to add regression check if code-level

## Hard rules

- Never lower severity. RLS leak = always blocker.
