# Overnight Operation Guide

> Jay starts Ralph and goes to sleep. Ralph + supervisor + medic handle everything,
> including rate-limits, crashes, and stalls. Morning: one slash command shows
> the diff.

## Three-layer resilience

```
   ┌─────────────────────────────────────────┐
   │ supervisor.sh                           │  (restarts Ralph if it dies)
   │   max_restarts=10  cooldown=60s         │
   │   crash_limit=5 within 30min → give up  │
   └─────────────────────────────────────────┘
                       │
                       ▼
   ┌─────────────────────────────────────────┐
   │ ralph.sh (200 iter cap)                 │
   │   per-iter rate-limit detection         │
   │   exp backoff: 30s → 60s → 90s → 120s   │
   │   max 5 attempts per iter               │
   └─────────────────────────────────────────┘
                       │
                       ▼
   ┌─────────────────────────────────────────┐
   │ claude -p (each iter)                   │
   │   single decision, single change        │
   │   exits cleanly to next iter            │
   └─────────────────────────────────────────┘
                       │
                       ▼
   ┌─────────────────────────────────────────┐
   │ curator-medic (every 10 iter)           │
   │   diagnoses 6 patterns                  │
   │   appends LEARNINGS.md                  │
   │   gentle recovery                       │
   └─────────────────────────────────────────┘
```

## Start (before sleep)

> **No `chmod +x` needed.** Every script is invoked as `bash <path>` so file
> mode doesn't matter. This avoids the chmod permission prompt entirely.

```bash
# (one-time) Apply permission patch
bash products/sediment/harness/permissions/apply.sh

# (one-time per machine) Bootstrap
bash products/sediment/harness/bootstrap-all.sh

# Overnight run — supervisor + Ralph in background
nohup bash products/sediment/harness/ralph/supervisor.sh \
  --max-restarts 15 --cooldown 120 \
  > output/ralph/supervisor.log 2>&1 &
echo $! > output/ralph/supervisor.pid

# Optional: dashboard refresher
nohup bash products/sediment/harness/monitor/dashboard-loop.sh \
  > output/ralph/dashboard.log 2>&1 &
```

## What happens during the night

| Event | Handler | Recovery time |
|---|---|---|
| Anthropic rate-limit (429) | ralph.sh exp backoff | 30s-150s |
| Network timeout | ralph.sh retry | 30s |
| Service died (ingester crash) | curator-medic detects + dispatches curator-fixer | 1-2 iter |
| Stalled task (5 iter no progress) | curator-medic appends LEARNINGS, escalates | 1 iter |
| Score regression | curator-medic dispatches matching specialist | 1-2 iter |
| Out of API budget | ralph stops cleanly with `cost_budget_exhausted` | terminal — manual |
| Ralph itself dies (oom etc.) | supervisor.sh restarts after cooldown | 60-300s |
| 5 crashes in 30min | supervisor writes CRASH_REPORT.md and stops | terminal — manual |

## Morning ritual

```bash
# 1. One-line status
/curator:status

# 2. If supervisor stopped (CRASH_REPORT exists)
cat output/ralph/CRASH_REPORT.md

# 3. If Ralph converged
cat output/validation/loop-*-*/convergence.md

# 4. If still running but stuck
/curator:medic
/curator:learnings --tail 20

# 5. To restart anything from any state
/curator:restart
```

## Cost ceiling

- ralph.sh: stops at `cost_budget_usd` (default $100, configurable)
- supervisor: doesn't restart on `cost_budget_exhausted`
- claude -p calls Sonnet by default ($3/Mtok in / $15/Mtok out)
- Estimated: ~$0.30-0.50 per iter × 200 max = $60-100 per overnight run
- Set tighter: `--cost-budget 30` for short runs

## Safety rails (auto-applied)

- Recipes never auto-fix RLS leaks (release_block) — write to work-order
- Recipes never auto-fix RAG quality / security — specialist subagents only
- LEARNINGS.md is append-only (never deleted)
- JOURNAL.md compacted to .archive.md when > 5000 lines
- service restart uses SIGTERM, never SIGKILL
- Dangerous commands denied at permission layer (DROP/TRUNCATE/rm -rf)

## If you wake up to chaos

```bash
# Hard stop everything
/curator:kill

# Read what happened
cat products/sediment/harness/ralph/JOURNAL.md | tail -50
cat products/sediment/harness/ralph/LEARNINGS.md | tail -30
ls -la output/ralph/iter-*.log | tail -5

# Reset Ralph state (preserves DB, services, learnings)
make ralph-reset

# Try again with tighter budget
nohup bash products/sediment/harness/ralph/supervisor.sh \
  --max-restarts 5 --cooldown 300 \
  > output/ralph/supervisor.log 2>&1 &
```
