---
name: curator-validate
description: >
  Run Sediment phase validation via subagent dispatch. Single-shot or 50-iteration
  self-improving loop. Project-agnostic — works for any project whose harness contract
  matches (rubric.yaml + e2e_spec.yaml + recipes.yaml).
user_invocable: true
triggers:
  - "/curator-validate"
  - "validate phase"
  - "check phase completion"
---

## Purpose

Single entry into Sediment's validation system. Dispatches to the right subagent
based on `mode` (single shot vs loop) and `focus` (rag/rls/e2e/all). Project-portable.

## Argument grammar

```
/curator-validate <phase> [mode] [focus]

phase    : p0 | p1 | p2 | p3   (required)
mode     : single | loop        (default: single)
focus    : all | rag | rls | e2e   (default: all)
```

Examples:
- `/curator-validate p1` — single-shot P1
- `/curator-validate p2 loop` — 50-iter convergence loop on P2
- `/curator-validate p1 single rls` — only run L3 (RLS) layer in P1
- `/curator-validate p1 loop rag` — loop, but only RAG layer

## Workflow

### Step 1 — Resolve mode → subagent

| mode | subagent | purpose |
|---|---|---|
| `single` | `curator-validator` | one rubric pass, return report |
| `loop` | `curator-loop-orchestrator` | 50-iter with auto-fix + specialist dispatch |

### Step 2 — Resolve focus → layer filter

| focus | layers passed |
|---|---|
| `all` | (none — all layers) |
| `rag` | L4 only |
| `rls` | L3 only |
| `e2e` | L11 only |
| `security` | L6 only |

### Step 3 — Dispatch via Task tool

For `mode=single`:
```
Task tool: subagent_type=curator-validator
prompt:
  Phase: {phase}
  Mode: single
  Focus layer: {focus mapped}
  Read products/sediment/VALIDATION_PLAN.md before starting.
  Return the JSON contract from your output spec.
```

For `mode=loop`:
```
Task tool: subagent_type=curator-loop-orchestrator
prompt:
  Phase: {phase}
  max_iter: 50
  target_pct: 95
  Read products/sediment/VALIDATION_PLAN.md before starting.
  Drive the loop to convergence (or stall/budget exhaustion).
  Return the JSON contract from your output spec.
```

### Step 4 — Surface result to user

After subagent returns:
- Show the JSON output verbatim (terse).
- If `next_action` indicates "proceed to PN+1", suggest the next slash invocation.
- If specialist dispatched, summarize their findings in 2 lines.
- If stalled / max_iter, link to the work-order.json and convergence.md.

## Hard rules

- Always pass the rubric.yaml path explicitly so this skill works in worktrees.
- Never invoke the harness Python directly from this skill. Always go via subagent
  so error handling / timeout / context budgeting is consistent.
- Never bypass `curator-validator`'s pre-flight check — services must be up before
  the rubric runs.

## Cross-project use

To use this skill in another project:

1. Copy `.claude/agents/curator-*.md` to the target project's `.claude/agents/`.
2. Copy `.claude/skills/curator-validate/` to the target project's `.claude/skills/`.
3. Copy the harness directory (`products/<product>/services/<svc>/validator/` or
   wherever you put it).
4. Edit the agent files' "First: Read Context" file paths to match the new project.
5. Rename the slash command in this SKILL.md (e.g., `/myapp-validate`).

The Python `harness/` is the only piece that needs significant porting. The agent
files (subagents, skill) are mostly text + path tweaks.
