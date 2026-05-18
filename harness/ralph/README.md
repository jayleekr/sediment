# Ralph Loop — Continuous Self-Evolution

> Pattern: Geoffrey Huntley's Ralph Wiggum loop (2026). A `while :; do` over
> `claude -p` calls that read a single prompt + state files, make ONE change,
> exit. The simplicity is the point. ([ghuntley.com/ralph](https://ghuntley.com/ralph/))

## What this gives you

- **Hands-off evolution** — Jay starts `./ralph.sh` once, walks away, comes back
  to a converged project (or stalled with a clear work-order).
- **No permission prompts** — each iteration runs as a subprocess with
  `--dangerously-skip-permissions`. The outer wrapper needs only basic shell perms
  (apply once via `harness/permissions/apply.sh`).
- **Cheap state** — three markdown/JSON files. No DB, no orchestrator service.
- **Composable with the existing 50-iter validator loop** — Ralph is the OUTER
  loop (phase progression, multi-phase). The validator loop is the INNER loop
  (rubric convergence within one phase).

## Files

| File | Purpose | Mutated by |
|---|---|---|
| `RALPH_PROMPT.md` | Master prompt; read EVERY iter | (read-only) |
| `TODO.md` | Phase tasks; checkboxes only | Ralph: append + check off |
| `JOURNAL.md` | Append-only log | Ralph: append 1-3 lines / iter |
| `STATE.json` | Machine state (iter count, cost) | Ralph: increment |
| `ralph.sh` | The dumb loop | (read-only) |

## Layered architecture

```
┌──────────────────────────────────────────────────────────────┐
│ L0  Permission patch (one-shot apply)                        │
│     harness/permissions/apply.sh                             │
├──────────────────────────────────────────────────────────────┤
│ L1  Ralph outer loop (this dir)                              │
│     ralph.sh ─→ claude -p --dangerously-skip-permissions     │
│     state: TODO.md + JOURNAL.md + STATE.json                 │
├──────────────────────────────────────────────────────────────┤
│ L2  Subagent dispatch (within each Ralph iter)               │
│     curator-validator / curator-loop-orchestrator /          │
│     curator-fixer / curator-rag-tuner /                      │
│     curator-rls-auditor / curator-e2e-debugger /             │
│     curator-rubric-author                                    │
├──────────────────────────────────────────────────────────────┤
│ L3  Validator harness (Python)                               │
│     services/sediment/validator/                              │
│     · runner.py (single-shot)                                │
│     · loop.py (50-iter convergence inner loop)               │
│     · dispatch.py (bash/sql/http/python/e2e)                 │
│     · e2e_runner.py (Playwright + screenshot)                │
│     · fixer.py + recipes.yaml                                │
├──────────────────────────────────────────────────────────────┤
│ L4  Project under test                                       │
│     services/sediment/applications/* (FastAPI 4 services)     │
│     web/src/app/curator/* (Next.js)                          │
│     infra/docker-compose.yml + init.sql                      │
└──────────────────────────────────────────────────────────────┘
```

## Quick start

> All commands invoke scripts as `bash <path>` — no `chmod +x` needed.

```bash
# 1. One-time: patch outer permissions (so wrapper doesn't prompt)
bash products/sediment/harness/permissions/apply.sh

# 2. Start Ralph under supervisor (auto-restart on crash, rate-limit aware)
nohup bash products/sediment/harness/ralph/supervisor.sh \
  > output/ralph/supervisor.log 2>&1 &
RALPH_PID=$!

# 3. Watch progress (in a 2nd terminal)
bash products/sediment/harness/monitor/watch.sh

# 4. When done (converged or stalled)
wait $RALPH_PID
cat products/sediment/harness/ralph/STATE.json | jq
```

## Stop conditions (any one triggers exit)

| Condition | Where checked | Result |
|---|---|---|
| `STOP` line in JOURNAL.md tail | every iter top | `stop_signal_in_journal` |
| All TODO.md checkboxes checked | every iter top | `all_todos_done` |
| iteration >= 200 (default) | every iter top | `max_iter_reached` |
| cumulative_cost >= $100 (default) | every iter top | `cost_budget_exhausted` |
| 5 consecutive iter no progress | every iter top | `stalled_5_iter` |
| SIGINT/SIGTERM | trap | `interrupted` |

`STATE.json.stop_reason` records which one.

## Resuming

```bash
# Continue from where we stopped (don't reset state files)
./ralph.sh --resume
```

If TODO.md was edited externally between runs, Ralph picks up the new items. If
JOURNAL.md has STOP, remove the STOP line first.

## Reset

```bash
# Erase state and start fresh
rm products/sediment/harness/ralph/{TODO.md,JOURNAL.md,STATE.json}
./ralph.sh   # auto-recreates from .template.* files
```

## Two-loop hierarchy

| Layer | Decides | Iterations | Termination |
|---|---|---|---|
| **Ralph (outer)** | Which phase / which task | up to 200 | TODO empty OR STOP OR stall |
| **Validator (inner)** | Rubric convergence within a phase | up to 50 | score ≥ 95% OR blockers OR stall OR cost |

Ralph picks the next TODO and dispatches to a subagent (often
`curator-loop-orchestrator`). That subagent runs the inner loop. When the inner
loop returns, Ralph reads the result, updates TODO/JOURNAL, and decides the next
move on its NEXT iter (fresh subprocess).

## Philosophy

> "Ralph is dumb on purpose. Don't try to make him smart. The model gets smart
> for one iteration, commits its decision to the markdown files, then dies. The
> next iter starts fresh. State machines outlive context windows."
> — adapted from Huntley, *Ralph Wiggum as a "software engineer"* (2026)

Why this beats a smart orchestrator:
- Context window drift across long sessions → markdown state outlives it
- Single-LLM error compounding → each iter is independent
- Permission/auth fatigue → subprocess pattern bypasses
- Cost predictability → linear in iter count, capped
