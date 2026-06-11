---
name: sediment:fix
description: Apply auto-fix recipes from the latest work-order.json. Restarts services, re-applies init.sql, etc. Never modifies source code.
user_invocable: true
triggers:
  - "/sediment:fix"
  - "apply work order"
---

## Purpose

Dispatch `sediment-fixer` to drain the latest work-order.

## Workflow

```bash
# Find latest work-order.json
WO=$(ls -1t output/validation/loop-*/iter-*/work-order.json 2>/dev/null | head -1)
[ -z "$WO" ] && WO=$(ls -1t output/validation/work-order-*.json 2>/dev/null | head -1)
echo "$WO"
```

Dispatch via Task tool:
```
subagent_type: sediment-fixer
prompt:
  work_order_path: <WO from above>
  Apply matching recipes from harness/recipes.yaml.
  Return JSON output contract.
```

If WO empty: tell user there's nothing to fix; suggest `/sediment:status` or `/sediment:medic`.

## Hard rules

- Don't run if no work-order exists.
- After fixer returns: suggest re-running validator for the affected phase.
