---
name: curator:restart
description: Resume Ralph after rate-limit, crash, or sleep. Reads STATE.json + LEARNINGS.md, decides whether to resurrect services or just resume the loop.
user_invocable: true
triggers:
  - "/curator:restart"
  - "resume ralph"
  - "wake up curator"
---

## Purpose

The "I came back, pick up where you left off" command. Designed to be the FIRST
thing Jay runs in the morning after overnight Ralph runs.

## Workflow

### Step 1 — Read state
```bash
STATE="products/sediment/harness/ralph/STATE.json"
[ ! -f "$STATE" ] && { echo "no STATE.json — run /curator:start first"; exit 1; }

iter=$(jq -r '.iteration' "$STATE")
reason=$(jq -r '.stop_reason // "running"' "$STATE")
phase=$(jq -r '.current_phase // "-"' "$STATE")
last_action=$(jq -r '.last_action // "-"' "$STATE")
echo "Last state: iter=$iter phase=$phase stop=$reason last_action=$last_action"
```

### Step 2 — Decide based on stop_reason

| stop_reason | action |
|---|---|
| `converged`, `all_todos_done` | Tell user "already done — see convergence.md". Don't restart. |
| `cost_budget_exhausted` | Ask user to raise budget OR resume with new budget. |
| `stalled_5_iter` | Run `/curator:medic` first to diagnose stall, then restart with `--resume`. |
| `max_iter_reached` | Run `/curator:medic`, examine TODO, then restart fresh OR with `--resume`. |
| `rate_limit*` (in last_action) | Wait 60s, then restart with `--resume`. |
| `interrupted` | Just restart with `--resume`. |
| (any crash) | Read `output/ralph/CRASH_REPORT.md` if exists. Run health checks. Restart with supervisor. |

### Step 3 — Health pre-checks
```bash
# Services
for kv in "5433:postgres" "6380:redis" "10100:platform" "10020:langgraph" "11000:ingester" "12000:metadata"; do
  port="${kv%%:*}"; name="${kv##*:}"
  nc -z localhost "$port" 2>/dev/null || echo "DOWN: $name :$port"
done | grep DOWN
```

If anything is down: invoke `/curator:resurrect all` first.

### Step 4 — Restart via supervisor
```bash
nohup bash products/sediment/harness/ralph/supervisor.sh \
  --max-restarts 10 --cooldown 60 --resume \
  > output/ralph/supervisor.log 2>&1 &
echo $! > output/ralph/supervisor.pid
echo "supervisor restarted pid=$!"
```

(Supervisor handles transient crashes; Ralph itself handles rate-limit backoff
inside each iter.)

### Step 5 — Surface to user
```
Resumed Ralph at iter=N (phase=PX). Supervisor pid=M. Watch with /curator:status.
```

## Hard rules

- Never wipe state files on restart unless user passes `--reset` (which we don't expose here).
- Never restart if `stop_reason=converged` — that's success, not a crash.
- If 3+ recent crashes in CRASH_REPORT.md: alert user before auto-restarting.
