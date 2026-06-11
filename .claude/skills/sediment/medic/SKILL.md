---
name: sediment:medic
description: Self-healing — scans Ralph state for stuck patterns, applies gentle recovery, appends LEARNINGS. Run on demand or every 10 iter.
user_invocable: true
triggers:
  - "/sediment:medic"
  - "sediment self-heal"
  - "ralph stuck"
---

## Purpose

Single trigger for the self-healing flow. Dispatches `sediment-medic` subagent.
Use when Ralph appears stuck or every 10 iter as preventive maintenance.

## Workflow

Dispatch via Task tool:
```
subagent_type: sediment-medic
prompt:
  Read products/sediment/harness/ralph/{STATE.json,JOURNAL.md,LEARNINGS.md}
  and apply at most ONE recovery action per the 6 canonical patterns
  (service_down, stalled_task, repeating_error, state_drift,
   score_regression, journal_overflow).
  Return the JSON output contract from your spec.
```

Surface the medic's JSON output verbatim. If `escalation` is non-null, present
the escalation reason and suggested next slash command.

## Hard rules

- Don't run more than once concurrently (medic locks STATE.json).
- If medic returns `journal_overflow`, suggest `/sediment:compact` next.
- If medic returns `score_regression(RLS)`, surface as 🚨 critical and suggest
  `/sediment:audit-rls`.
