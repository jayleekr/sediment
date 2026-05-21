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

> ⚠️ The Directory Map further down predates the 2026-05-18 split out of
> `jayleekr/hypeprooflab`. Actual paths now: this repo root → `services/sediment/`
> (no `products/` prefix) and `frontend/` (Sediment UI) instead of `web/src/app/curator/`.
> Routes under `frontend/app/sediment/` not `/curator`. See `CLAUDE.md` for current layout.

---

## Quick Start (local, 5 minutes)

Prerequisites: Docker, Python 3.11+, Node 20+, OpenAI API key (embedding), Anthropic API key (LLM, optional — runs in offline mode without).

```bash
# 1. From repo root
cd products/sediment

# 2. Configure
cp .env.example .env
# edit .env — set ANTHROPIC_API_KEY and OPENAI_API_KEY (optional but recommended)

# 3. Start Postgres + Redis
make up

# 4. Install Python deps
make install

# 5. Seed default tenant + members from data/members.json
make seed

# 6. In 4 separate terminals, start the services:
make platform      # :10100  REST API
make langgraph     # :10020  SSE stream
make ingester      # :11000  vault ingest
make metadata      # :12000  metadata queries

# 7. Initial vault ingest (research/, columns/, novels/)
make ingest

# 8. Verify cross-tenant isolation
make verify-rls

# 9. Run the web UI
cd ../../web && npm run dev
# open http://localhost:3000/curator
```

---

## Architecture (8명 팀 → SaaS multi-tenant ready)

```
                      ┌─────────────────────────┐
                      │  Next.js /curator/*     │  (web/)
                      │  chat · library · mem   │
                      └──────────┬──────────────┘
                                 │ JWT
       ┌─────────────────────────┼─────────────────────────┐
       │                         │                         │
┌──────▼──────┐         ┌────────▼────────┐        ┌───────▼────────┐
│  platform   │         │   langgraph     │        │  ingester      │
│  :10100     │         │   :10020 SSE    │        │  :11000        │
│  REST       │         │   lab_curator   │        │  RAG ingest    │
│             │         │   _graph        │        │                │
│ tenant ctx  │         │                 │        │ service role   │
│ middleware  │         │  Workspace MCP  │        │ (BYPASSRLS)    │
└──────┬──────┘         │  :8888          │        └───────┬────────┘
       │                │  12 tools       │                │
       │                └────────┬────────┘                │
       │                         │                         │
       └─────────────────────────▼─────────────────────────┘
                          ┌─────────────┐
                          │  Postgres   │  pgvector + RLS
                          │   :5433     │  ┌──────────────────┐
                          │             │  │ All tables have  │
                          │   curator   │  │ tenant_id NOT    │
                          │     DB      │  │ NULL + policies  │
                          │             │  └──────────────────┘
                          └─────────────┘
                          ┌─────────────┐
                          │   Redis     │  :6380 cache/SSE
                          └─────────────┘
```

**Multi-tenant guarantees:**
1. Every tenant-scoped table has `tenant_id UUID NOT NULL`.
2. Postgres RLS policies (`USING tenant_id = current_tenant_id()`) enforce isolation.
3. App role (`curator_app`) is subject to RLS. Service role (`curator_service`) is `BYPASSRLS` — used only by ingest/cron/admin.
4. `TenantContextMiddleware` on every request: JWT → `SET LOCAL app.tenant_id`.
5. `make verify-rls` runs cross-tenant checks (insert markers in 2 tenants, assert no leakage).

---

## Directory Map

```
products/sediment/
├── SPEC.md                      # full design doc (v0.2)
├── DECISIONS.md                 # all §11 questions answered
├── README.md                    # this file
├── Makefile                     # `make <target>`
├── .env.example
├── .gitignore
│
├── infra/
│   ├── docker-compose.yml       # Postgres + Redis
│   ├── init.sql                 # DDL + RLS policies + roles
│   ├── launchd/                 # macOS cron plists
│   └── terraform/               # Phase 9+ deploy stub
│
├── services/sediment/            # Python FastAPI monorepo
│   ├── pyproject.toml
│   ├── lab_lib/                 # shared
│   │   ├── settings.py
│   │   ├── logging.py
│   │   ├── db.py                # async session + tenant context
│   │   ├── auth.py              # JWT mint/decode
│   │   ├── tenant_middleware.py
│   │   ├── embeddings.py        # OpenAI text-embedding-3-small
│   │   └── chunker.py           # heading-aware Markdown chunker
│   ├── applications/
│   │   ├── curator_platform/    # :10100 REST
│   │   │   ├── main.py
│   │   │   └── routers/         # auth, conversations, library, members,
│   │   │                        # ingest_proxy, feedback, costs, admin,
│   │   │                        # onboarding, billing
│   │   ├── curator_langgraph/   # :10020 SSE
│   │   │   ├── main.py
│   │   │   └── graphs/lab_curator_graph.py
│   │   ├── vault_ingester/      # :11000
│   │   │   └── main.py
│   │   └── metadata_svc/        # :12000
│   │       └── main.py
│   ├── lab_platform/
│   │   ├── mcp_servers/
│   │   │   └── workspace_mcp.py # 12 tenant-aware tools
│   │   └── agents/              # (Phase 4 expansion)
│   ├── scripts/
│   │   ├── seed_lab.py
│   │   ├── ingest_repo.py
│   │   ├── verify_rls.py
│   │   ├── discord_ingest.py
│   │   └── cron/
│   │       ├── daily_ingest.sh
│   │       ├── retro.py
│   │       └── dream.py
│   ├── tests/                   # pytest
│   │   ├── test_rls.py
│   │   ├── test_chunker.py
│   │   └── test_auth.py
│   └── migrations/              # alembic (placeholder for Phase 6)
│
├── workspace_solutions/         # Phase 10+ enterprise dedicated
│   └── _template/
│       ├── README.md
│       └── tenant.yaml
│
└── docs/                        # extra design/runbook docs (empty)
```

The Next.js side lives in `web/src/app/curator/`:
```
web/src/app/curator/
├── layout.tsx
├── page.tsx                    # chat home + dev sign-in
├── c/[id]/page.tsx             # conversation + SSE
├── library/page.tsx
├── members/page.tsx
├── admin/page.tsx
├── onboard/page.tsx            # Phase 6 wizard stub
├── pricing/page.tsx            # Phase 8 pricing page stub
├── auth/README.md              # NextAuth.js guide for Phase 5
└── lib/
    ├── api.ts
    └── sse.ts
```

---

## 5 Validation Queries (MVP gate, SPEC §6.3)

After `make ingest`, sign in as Jay (`jay.lee@sonatus.com`) and ask:

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
