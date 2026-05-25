# Sediment

> HypeProof Lab's evidence-grounded memory layer — "where doing becomes knowing".
> Built from the AI Technician (Sonatus) architecture pattern: LangGraph + pgvector + domain MCP + tenant-aware everything.
> Designed to scale into a multi-tenant SaaS.

**Live (2026-05-20):**
- UI → https://sediment.hypeproof-ai.xyz/sediment (standalone Next.js on Vercel)
- API → https://hypeproof-sediment.fly.dev (Fly NRT, single-VM multi-service)
- Old URL → https://web-nu-seven-39.vercel.app/sediment (307→new domain)
- CD → main push triggers `fly deploy` + post-deploy E2E-12 smoke
  (`.github/workflows/fly-deploy.yml`)

**Status**: MVP live + first dogfood release. Phases 0-5 wired and shipped; Phases 6-9 stubbed UI + endpoints; Phase 10+ placeholders.

| Phase | Status | What's wired |
|---|---|---|
| 0. Spec & Scaffolding | ✅ | DECISIONS.md, dirs, docker, init.sql with RLS |
| 1. Read-only index | ✅ | vault-ingester (RAG), metadata-svc, seed/ingest scripts |
| 2. Chat MVP | ✅ | sediment-platform 10 routers, sediment-langgraph SSE, workspace_curator_graph, Workspace MCP 12 tools, standalone Next.js `frontend/app/sediment/*` |
| 3. Ingest automation | ✅ | APScheduler in-VM (`config/cron.yaml`, 8 Discord channels every 30 min), webhook endpoints (`/webhook/ingest`, `/webhook/discord-ingest`), `consolidate_memory.py`, `distill.py` |
| 4. Memory consolidation | ✅ | dream.py (archive + boost + decision/action extraction + usage rollup) |
| 5. Auth + RBAC + RLS test | ✅ | dev-token + GitHub OAuth (prod), pytest cross-tenant verify |
| 5.5. Dogfood gate | 🟢 active | `PHASE_5_5_DOGFOOD_GATE.md` — 10 criteria, measurement begins on `feature_flags.dogfood_gate_active = true` |
| 6. Tenant onboarding | 🟡 stub | UI wizard + onboarding API endpoint |
| 7. Stripe + quota | 🟡 stub | webhook handler + checkout endpoint stub |
| 8. Beta launch | 🟡 stub | pricing page |
| 9. GA + pricing | 🟡 stub | (deployment-time work) |
| 10+. Enterprise | 📁 placeholder | workspace_solutions/_template + terraform stub |

---

## Quick Start (local, 5 minutes)

Prerequisites: Docker, Python 3.11+, Node 20+, OpenAI API key (embeddings), Anthropic or Gemini API key (LLM — runs in offline mock mode without).

```bash
# 1. From repo root (this repo IS the project; no products/ prefix anymore)
cd services/sediment

# 2. Configure
cp .env.example .env
# edit .env — set ANTHROPIC_API_KEY (or GEMINI_API_KEY) and OPENAI_API_KEY

# 3. Start Postgres + Redis (from repo root, not services/sediment)
cd .. && cd .. && make -C . up   # or just: docker compose -f infra/docker-compose.yml up -d

# 4. Install Python deps
cd services/sediment && uv sync   # or `pip install -e .`

# 5. Seed default tenants + members from data/members.json
.venv/bin/python -m scripts.seed_lab

# 6. Start the 4 services + scheduler (5 terminals; or supervisord in prod)
.venv/bin/uvicorn applications.sediment_platform.main:app  --port 10100 --reload
.venv/bin/uvicorn applications.sediment_langgraph.main:app --port 10020 --reload
.venv/bin/uvicorn applications.vault_ingester.main:app     --port 11000 --reload
.venv/bin/uvicorn applications.metadata_svc.main:app       --port 12000 --reload
.venv/bin/python  -m scripts.scheduler                              # APScheduler cron

# 7. Initial vault ingest (point at the hypeprooflab content repo)
.venv/bin/python -m scripts.ingest_repo --root /path/to/hypeprooflab

# 8. Verify cross-tenant isolation
.venv/bin/python -m scripts.verify_rls

# 9. Run the web UI
cd ../../frontend && npm install && npm run dev
# open http://localhost:3000/sediment
```

For prod deploy: just `git push origin main` — see `.github/workflows/fly-deploy.yml` and `infra/deploy/README.md`.

---

## Architecture (8명 팀 → SaaS multi-tenant ready)

```
                    ┌──────────────────────────────┐
                    │  Next.js  /sediment/*        │  (frontend/, Vercel)
                    │  chat · library · members    │
                    └───────────────┬──────────────┘
                                    │ JWT  (HTTPS)
                ┌───────────────────┼─────────────────────────────┐
                │                   │                             │
        ┌───────▼────────┐ ┌────────▼─────────┐ ┌─────────────────▼──────────┐
        │  platform      │ │  langgraph       │ │  ingester                  │
        │  :10100  REST  │ │  :10020 SSE      │ │  :11000  /webhook/*        │
        │  workspace_    │ │  curator_graph   │ │  + RAG batch ingest        │
        │  curator_graph │ │  Workspace MCP   │ │                            │
        │  tenant_ctx    │ │  12 tools        │ │  service role (BYPASSRLS)  │
        │  middleware    │ │                  │ │                            │
        └───────┬────────┘ └──────────┬───────┘ └───────────────┬────────────┘
                │                     │                         │
                │             ┌───────▼──────────┐              │
                │             │ scheduler.py     │              │
                │             │ (APScheduler)    │              │
                │             │ Discord :30m +   │              │
                │             │ distill hourly + │              │
                │             │ consolidate 12h  │              │
                │             └───────┬──────────┘              │
                │                     │                         │
                └─────────────────────┼─────────────────────────┘
                                      ▼
                          ┌─────────────────────────┐
                          │  Supabase Postgres      │  pgvector 0.8.0 + HNSW
                          │  (pooler :5432)         │  + RLS 14 tables
                          │                         │  ┌─────────────────────┐
                          │  690 artifacts          │  │ All tenant tables   │
                          │  6469 chunks            │  │ have tenant_id NOT  │
                          │                         │  │ NULL + policies     │
                          └─────────────────────────┘  └─────────────────────┘

  Internal-only (not routed):  metadata-svc :12000 (validator queries)
  Production: 5 services + nginx packed into a single Fly VM (NRT) via supervisord;
              fronted by nginx :8080 → platform/langgraph/ingester via path routing.
```

**Multi-tenant guarantees:**
1. Every tenant-scoped table has `tenant_id UUID NOT NULL`.
2. Postgres RLS policies (`USING tenant_id = current_tenant_id()`) enforce isolation.
3. App role (`curator_app`) is subject to RLS. Service role (`curator_service`) is `BYPASSRLS` — used only by ingest/cron/admin. (Dogfood currently runs as `postgres` superuser on Supabase — single-tenant phase only.)
4. `TenantContextMiddleware` on every request: JWT → `SELECT set_config('app.tenant_id', ...)`.
5. `verify_rls.py` runs cross-tenant checks (insert markers in 2 tenants, assert no leakage).

---

## Directory Map (post 2026-05-18 repo split — this is the live layout)

```
sediment/                              # repo root (was: products/sediment/)
├── SPEC.md                            # full design doc
├── DECISIONS.md                       # §11 questions answered + ongoing
├── NEXT.md                            # post-MVP roadmap + ops baseline
├── PHASE_5_5_DOGFOOD_GATE.md          # dogfood gate criteria
├── CLAUDE.md                          # per-project AI-agent guardrails
├── Dockerfile                         # multi-svc image (nginx + 5 uvicorns)
├── README.md                          # this file
│
├── .claude/
│   └── guard.json                     # tool-level edit protection (init.sql, .env, billing.py)
│
├── .github/workflows/
│   ├── fly-deploy.yml                 # main push → fly deploy + E2E-12 smoke
│   └── nightly-recall.yml             # daily recall@3 sweep
│
├── frontend/                          # Standalone Next.js 16 UI (Vercel project)
│   ├── package.json
│   ├── next.config.ts                 # turbopack + (dev) /api/v1 proxy to Fly
│   └── app/
│       ├── layout.tsx                 # root + env-aware badge
│       ├── auth.ts                    # GitHub OAuth via NextAuth (Phase 5)
│       └── sediment/                  # all 9 routes (chat, library, members, admin, ...)
│
├── infra/
│   ├── init.sql                       # DDL + RLS policies + roles (guard.json blocks edits)
│   ├── docker-compose.yml             # local dev: pgvector pg17 + redis
│   ├── deploy/                        # production deploy (Fly)
│   │   ├── fly.toml                   # app config (NRT, 1 VM, 1024MB)
│   │   ├── start.sh                   # entrypoint: normalize DB URL, exec supervisord
│   │   ├── release.sh                 # fly release_command — idempotent seed_lab
│   │   ├── nginx.conf                 # :8080 routing + /proxy/anthropic egress
│   │   ├── supervisord.conf           # 4 uvicorn + scheduler + nginx
│   │   ├── run-with-db.sh             # admin wrapper (DB URL normalize)
│   │   └── README.md                  # first-deploy runbook
│   ├── github-actions/
│   │   └── vault-ingest.yml           # template: install into hypeprooflab repo
│   ├── launchd/                       # intentionally empty (in-VM APScheduler replaces)
│   ├── terraform/                     # Phase 9+ deploy stub (README only)
│   └── SUPABASE_MIGRATION.md          # P2 cutover guide (done 2026-05-21)
│
├── services/sediment/                 # Python FastAPI services
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── lab_lib/                       # shared
│   │   ├── settings.py
│   │   ├── logging.py
│   │   ├── db.py                      # async session + tenant context
│   │   ├── auth.py                    # JWT mint/decode
│   │   ├── tenant_middleware.py
│   │   ├── embeddings.py              # OpenAI text-embedding-3-small
│   │   ├── chunker.py                 # heading-aware Markdown chunker
│   │   ├── prompts.py                 # YAML strategy loader (distill, governance)
│   │   ├── vault_paths.py             # ref/path helpers
│   │   ├── cost_tracker.py            # llm_calls table + daily rollup
│   │   └── connectors/                # Discord HTTP + base connector
│   ├── applications/
│   │   ├── sediment_platform/         # :10100 REST (auth, library, members, admin, billing, ...)
│   │   ├── sediment_langgraph/        # :10020 SSE (workspace_curator_graph)
│   │   ├── vault_ingester/            # :11000 (webhook/* batch ingest)
│   │   ├── metadata_svc/              # :12000 (internal validator queries)
│   │   └── sediment_mcp/              # FastMCP server (12 tools)
│   ├── lab_platform/
│   │   └── mcp_servers/workspace_mcp.py
│   ├── prompts/                       # YAML strategies (loaded by lab_lib/prompts.py)
│   │   ├── distill/                   # base.yaml + 4 strategies (chat_thread, doc_edit, ...)
│   │   └── governance/                # base.yaml + 3 strategies (anomaly_flag, ...)
│   ├── config/
│   │   └── cron.yaml                  # APScheduler schedule (8 Discord channels, etc.)
│   ├── scripts/
│   │   ├── seed_lab.py                # idempotent tenant/member seed
│   │   ├── ingest_repo.py             # bulk vault ingest
│   │   ├── verify_rls.py
│   │   ├── discord_fetch.py           # cron HTTP fetcher
│   │   ├── discord_ingest.py          # legacy fixture-based ingester
│   │   ├── distill.py                 # per-source distill (uses prompts.py)
│   │   ├── consolidate_memory.py      # conv → decisions/actions
│   │   ├── scheduler.py               # APScheduler daemon
│   │   ├── dogfood_digest.py
│   │   ├── reingest_to.sh
│   │   └── cron/dream.py              # Sunday memory consolidation (legacy)
│   ├── data/
│   │   └── members.json               # baked into image; release_command upserts
│   ├── tests/
│   │   ├── test_rls.py
│   │   ├── test_chunker.py
│   │   ├── test_auth.py
│   │   └── test_prompts.py
│   └── validator/
│       ├── rubric.yaml                # check definitions (parent-only edits)
│       ├── recipes.yaml               # 4-tier code-mod policy
│       ├── e2e_spec.yaml              # 12 Playwright flows, multi-env (dev/prod)
│       ├── golden_queries.yaml        # 40-query RAG eval set
│       ├── ux_rubric.yaml
│       ├── runner.py / loop.py / e2e_runner.py / report.py / fixer.py / dispatch.py
│       ├── checks/                    # per-check Python (p3_automation, lib_rag, ...)
│       └── scripts/
│           └── recall_live.py         # live recall@3 (used by nightly-recall.yml)
│
├── harness/
│   ├── ralph/                         # autonomous self-improving loop
│   │   ├── ralph.sh / supervisor.sh
│   │   ├── RALPH_PROMPT.md            # agent contract
│   │   ├── LEARNINGS.md               # append-only memory across iterations
│   │   └── *.template.*               # restored each iter
│   ├── scripts/                       # ai-commit.sh, lint-sql-cast.sh, restart-services-if-changed.sh
│   └── contracts/ / monitor/ / permissions/ / templates/
│
└── docs/
    └── design/
        └── collection-and-distillation.md  # v0.3 design (Collection + Data Governance Agent)
```

---

## 5 Validation Queries (MVP gate, SPEC §6.3)

After `make ingest`, sign in as Jay (`jayleekr0125@gmail.com`) and ask:

1. **"라이언이 4월에 쓴 mirror-loop 칼럼"** → filtered library + RAG
2. **"Daily research 중 Claude Code 관련 high-confidence 결론"** → frontmatter filter + RAG
3. **"JY가 작성한 글 중 agent 관련 주제"** → author + topic match
4. **"최근 결정된 5/5 파일럿 관련 액션"** → decisions/actions tables
5. **"지난 30일 신규 칼럼 수"** → metadata summary

If all 5 return reasonable answers with citations: Phase 1+2 gate passed.

---

## RLS Verification Drill (must pass before any external onboarding)

```bash
# Manual:
make verify-rls
# Expected: "rls.ok — cross-tenant isolation verified"

# Automated (pytest):
make test
# Expected: tests/test_rls.py PASSED
```

Then sign in as different members from different tenants in the web UI and confirm:
- Tenant A user cannot see Tenant B's library or conversations
- API calls with mismatched tenant in JWT return 401/empty results

---

## Cron Jobs (macOS launchd)

Install:
```bash
cp infra/launchd/com.hypeproof.sediment.*.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.hypeproof.sediment.daily-ingest.plist
launchctl load -w ~/Library/LaunchAgents/com.hypeproof.sediment.dream.plist
```

| Job | Schedule | What it does |
|---|---|---|
| daily_ingest | 06:30 KST | Pull repo, ingest changed markdown |
| retro | (add plist) 22:00 KST | Per-tenant daily summary |
| dream | Sun 02:00 KST | Archive old episodic, boost cited chunks, extract decisions/actions, roll usage |

---

## SaaS Multi-Tenant — what's already in place

✅ `tenants`, `subscriptions`, `integrations` tables
✅ All other tables have `tenant_id UUID NOT NULL` + RLS
✅ Two DB roles: `curator_app` (RLS) and `curator_service` (BYPASSRLS)
✅ `TenantContextMiddleware` on every request
✅ Onboarding API endpoint (`POST /api/v1/onboard`)
✅ Stripe webhook stub (`POST /api/v1/billing/webhook`)
✅ Admin dashboard listing all tenants
✅ Seat / quota fields in `subscriptions`

✅ = data model + endpoint exists
🟡 = UI exists, real wiring needed when first paid customer arrives

What needs real wiring at "Phase 6 → external beta" time:
- Replace dev-token with NextAuth.js + Discord/Email providers
- Real Stripe SDK calls (test mode → live)
- Onboarding wizard backend → real ingest job queue
- Email delivery (Resend) for invites
- Quota enforcement middleware (currently no rate limit per query)
- Brand toggle wiring (hide "Powered by HypeProof" for paid tiers)

---

## Next Steps (priority order)

1. **Run end-to-end on Jay's machine** — `make up && make install && make seed && make ingest`, run all 4 services, sign in, ask the 5 validation queries.
2. **Tune chunking** if retrieval is poor (max_tokens / overlap).
3. **Add Discord ingest** — point `discord_ingest.py` at Mother bot's MCP plugin instead of fixture file.
4. **Phase 5.5 dogfood gate (4 weeks)** — measure NPS, query/seat/day, RLS leak count (must be 0).
5. **Phase 6 trigger** — first external customer expresses interest → wire NextAuth.js + onboarding backend.

---

## Files of interest

- [SPEC.md](./SPEC.md) — full design (v0.2 with §12 commercialization path)
- [DECISIONS.md](./DECISIONS.md) — all 20 §11 questions answered with reasoning
- [infra/init.sql](./infra/init.sql) — DDL + RLS policies (copy/paste into Supabase later)
- [services/sediment/lab_platform/mcp_servers/workspace_mcp.py](./services/sediment/lab_platform/mcp_servers/workspace_mcp.py) — 12 domain tools
- [services/sediment/applications/sediment_langgraph/graphs/lab_curator_graph.py](./services/sediment/applications/sediment_langgraph/graphs/lab_curator_graph.py) — LangGraph workflow
- [services/sediment/scripts/verify_rls.py](./services/sediment/scripts/verify_rls.py) — RLS regression check

---

*Generated 2026-05-05. Built without Jay's input per `/remote-control` instruction. All §11 decisions in DECISIONS.md.*
