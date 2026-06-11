---
name: sediment:propose-rubric
description: Generate new rubric.yaml entries from a git diff. Use when a feature ships and rubric needs new checks.
user_invocable: true
triggers:
  - "/sediment:propose-rubric"
  - "new rubric checks"
  - "rubric drift"
---

## Args

```
/sediment:propose-rubric [<since-ref>] [<target-phase>]
```

Defaults: since_ref = `HEAD~10`, target_phase = next open phase from TODO.

## Workflow

Dispatch:
```
subagent_type: sediment-rubric-author
prompt:
  since_ref: <ref>
  target_phase: <phase>
  Diff the repo, classify changes (router / table / web route / agent / cron),
  draft yaml stanzas + python check stubs.
  Return JSON output contract; write proposal markdown.
```

Surface: count of new checks proposed + path to proposal markdown.

## Hard rules

- Never auto-apply to rubric.yaml. Proposal-only.
- Confirm severity classification matches TEST_REQUIREMENTS layer.
