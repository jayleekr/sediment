# Ralph TODO

> Append-only + check-off-only. Don't delete entries. Format:
> `- [ ] <id>: <description>` or `- [ ] blocked-by:<id> <id>: <description>`
> When done: `- [x]`

## Phase -1 — Environment self-heal (FIRST + on any env failure)

- [ ] P-1.setup: `bash products/sediment/harness/scripts/setup-env.sh` (idempotent — runs 8 stages: docker daemon, credential PATH, python 3.11+, venv, deps, playwright, .env, infra). Must exit 0 before any phase task. On failure, append LEARNINGS entry and STOP.

## Phase 0 — Scaffolding

- [ ] blocked-by:P-1.setup P0.boot: verify Postgres+Redis healthy (nc :5433, :6380)
- [ ] blocked-by:P0.boot P0.seed: run `cd products/sediment/services/sediment && .venv/bin/python -m scripts.seed_lab`
- [ ] blocked-by:P0.seed P0.validate: invoke `curator-validator` subagent for phase P0 single-shot → must converge

## Phase 1 — Read-only index

- [ ] blocked-by:P0.validate P1.ingester: start `make ingester` background
- [ ] blocked-by:P1.ingester P1.metadata: start `make metadata` background
- [ ] blocked-by:P1.ingester P1.ingest: `make ingest` (corpus build, ~5-10 min)
- [ ] blocked-by:P1.ingest P1.validate: `/curator-validate p1 loop` → converge
- [ ] blocked-by:P1.validate P1.golden-tune: if recall@3 < 80%, dispatch curator-rag-tuner

## Phase 2 — Chat MVP

- [ ] blocked-by:P1.validate P2.platform: start `make platform` background
- [ ] blocked-by:P1.validate P2.langgraph: start `make langgraph` background
- [ ] blocked-by:P1.validate P2.web: start `make web` background (Next.js dev)
- [ ] blocked-by:P2.web P2.validate: `/curator-validate p2 loop`
- [ ] blocked-by:P2.validate P2.e2e-stable: if E2E flake > 5%, dispatch curator-e2e-debugger

## Phase 3 — Automation (optional for solo MVP)

- [ ] blocked-by:P2.validate P3.validate: `/curator-validate p3 single` (cron + GHA + Discord)

## Code-mode tasks (AI-driven fixes — runs whenever validator score < 95%)

- [ ] CODE.always: Per iter, if any TIER-2 work-order exists in latest output/validation/, dispatch curator-coder with that work-order. Coder + reviewer + auto-commit. NEVER dispatch coder for TIER-3 (RLS) or TIER-4 (guard.json paths).
- [ ] CODE.gate: Before commit, branch must pass `bash harness/scripts/ai-commit.sh gate <CHECK_ID> <PHASE>`. Score must NOT regress vs baseline.
- [ ] CODE.review: Coder calls Task subagent_type=curator-reviewer with the branch. If verdict=approve, commit. If revise, ONE more attempt. If reject, write LEARNINGS + escalate.

## Convergence + journaling

- [ ] blocked-by:P2.validate REVIEW: append "STOP — phases converged" to JOURNAL when P2 converged
