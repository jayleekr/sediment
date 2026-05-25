# Sediment CLI stress harness

k6 scripts that exercise the API in the shape the CLI / shim will hit.

## Install k6
```bash
brew install k6
```

## Run a scenario locally
```bash
# 1. Start the stack
cd ../.. && make up
cd services/sediment && SEDIMENT_DEV_MODE=1 .venv/bin/uvicorn applications.sediment_platform.main:app --port 10101 &

# 2. Mint a JWT (or two for tenant-fairness scenarios)
JWT=$(curl -s -X POST http://localhost:10101/api/v1/auth/dev-token \
  -H 'Content-Type: application/json' \
  -d '{"email":"jayleekr0125@gmail.com"}' | jq -r .token)
JWT_ACME=$(curl -s -X POST http://localhost:10101/api/v1/auth/dev-token \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@acme.test"}' | jq -r .token)

# 3. Run a scenario
BASE_URL=http://localhost:10101 \
JWT="$JWT" JWT_ACME="$JWT_ACME" \
k6 run steady_state.js
```

## Scenarios

| File | What it measures | Pass criteria |
|---|---|---|
| `steady_state.js` | 10 VUs × 4 req/min × 30 min | p50 search < 400ms, p50 ask < 3s, errors < 0.5% |
| `ask_burst.js` | 20 concurrent SSE `ask` calls | all complete, no 5xx, langgraph RSS steady |
| `search_ramp.js` | 1 → 50 QPS over 5 min | find knee, document QPS limit |
| `reconnect_storm.js` | 100 sessions within 5s | no 5xx, nginx queue absorbs |
| `tenant_fairness.js` | Tenant A 80%, Tenant B 20% | Tenant B p95 < 2x its solo p95 |
| `cli_fork_cost.sh` | 50 parallel `sediment search` subprocs | median wall time ≤ 50ms each |

## Cost guardrail
The `ask` scenarios drive Anthropic spend. The local stack uses the lab
key — check `services/sediment/data/cost_log.jsonl` after each run to
confirm spend ≤ $0.50.

## Integration
- `cli-stress.yml` GH Actions workflow runs `steady_state.js` weekly Sunday
  against staging. Results posted to Discord.
- Manual run before each release: full matrix from the table above.
