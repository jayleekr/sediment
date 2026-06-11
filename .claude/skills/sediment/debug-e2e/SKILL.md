---
name: sediment:debug-e2e
description: Diagnose Playwright E2E failures. Reads screenshots from latest iter, classifies (selector_drift / race / auth / upstream), proposes patch.
user_invocable: true
triggers:
  - "/sediment:debug-e2e"
  - "e2e flaking"
  - "playwright failing"
---

## Args

```
/sediment:debug-e2e [E2E-NN]
```

If flow id given: only that flow. Otherwise: every failing flow in latest report.

## Workflow

```bash
ITER=$(ls -1td output/validation/loop-P2-*/iter-* 2>/dev/null | head -1)
[ -z "$ITER" ] && { echo "no P2 iter found"; exit 1; }
```

For each failed E2E-* in `$ITER/report.json`:

Dispatch (one per flow):
```
subagent_type: sediment-e2e-debugger
prompt:
  iteration_dir: <ITER>
  flow_id: <E2E-NN>
  Inspect screenshots, classify failure, write proposal.
```

Aggregate proposals in summary. List the 1-3 selector/wait changes that would unblock most flows.
