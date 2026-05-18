---
name: curator:start
description: One-shot bootstrap for Sediment (infra → deps → seed → services → P0 validate → optional Ralph). Idempotent.
user_invocable: true
triggers:
  - "/curator:start"
  - "curator bootstrap"
  - "start curator"
---

## Purpose

Run the full bootstrap pipeline so Jay never has to remember the order. Prefer this over running `make` targets manually.

## Args

```
/curator:start [--with-ralph]
```

`--with-ralph` also kicks off the Ralph loop in background after bootstrap.

## Workflow

### Step 1 — Pre-flight check
```bash
docker info >/dev/null 2>&1 || echo "DOCKER_DAEMON_DOWN"
test -f products/sediment/.env || echo "NO_ENV"
grep -q '^ANTHROPIC_API_KEY=sk-ant' products/sediment/.env 2>/dev/null || echo "NO_ANTHROPIC_KEY"
grep -q '^OPENAI_API_KEY=sk' products/sediment/.env 2>/dev/null || echo "NO_OPENAI_KEY"
```

If `DOCKER_DAEMON_DOWN`: tell user to start Docker Desktop and re-run.
If `NO_ENV`: copy `.env.example` to `.env`, then alert user to add keys.
If keys missing: bootstrap proceeds but warn that LLM-dependent checks will run in offline mode (zero vectors / mock responses).

### Step 2 — Run bootstrap script
```bash
bash products/sediment/harness/bootstrap-all.sh ${args}
# already uses 'bash' invocation — no chmod needed
```

This sequences 9 stages with idempotent skip-if-done logic. Each stage logs to `output/bootstrap/NN-stage.log`.

### Step 3 — Read final status
```bash
cat output/bootstrap/08-validate-p0.log | tail -30
```

Output to user:
- Which stages ran fresh vs were skipped
- P0 score (% and blockers)
- Background service PIDs (from `output/bootstrap/*.pid`)
- Where to view live monitor: `make monitor`

## Output format

```
✓ docker      [skipped — already up]
✓ deps        [installed]
✓ playwright  [installed]
✓ seed        [seeded 8 members + 2 tenants]
✓ ingester    [running pid=N]
✓ ingest      [42 artifacts, 178 chunks]
✓ services    [platform/langgraph/metadata up]
○ P0          [9/15 blockers passed — see output/validation/P0-latest.md]

Next: /curator:status   for live state
      /curator:medic    if any blocker failed
```

## Hard rules

- Never run `make reset` (drops DB).
- If bootstrap-all.sh hangs, kill after 10 min and read the log of the last stage.
- Don't proceed to Ralph if P0 has 5+ blocker failures — fix those first via /curator:fix.
