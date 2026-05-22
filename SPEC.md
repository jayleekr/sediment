# Sediment — Executive Spec

> **Sediment** — "where doing becomes knowing". Evidence-grounded memory layer for HypeProof Lab, designed multi-tenant from day one for SaaS scale-out.

This file is the 200-line orientation. Full design lives in **[`docs/design/`](./docs/design/)**.

---

## What it is

A single-VM multi-tenant memory engine that captures team activity (chat, docs, repos, voice, photos), distills it into citable evidence, and serves it back as cited chat. Every answer carries inline `[N]` references. Built for Korean SMBs that have no data layer at all — the founder's head + 카톡 + paper + meeting drift.

## Who it's for

- **Y1 dogfood**: HypeProof Lab (8 people) + Jinyong's Kids Edu team (5+ contributors)
- **Y1 paying tenants (30-50)**: D archetype (doer-architect founders, 5-10 person teams) + A archetype (small clinics like 치과, 20-50 staff)
- **Beyond Y1**: B archetype (50-200 SMB), C archetype (200+) on dedicated instances

## What it does

Four pipelines, all per-tenant, all RLS-isolated:

1. **Capture** — connector framework pulls from Discord, GitHub, future Slack/Notion/Drive + Phase A voice/OCR. PIPA-clean only — BYOData + admin OAuth. **No KakaoTalk auto-fetch ever.**
2. **Distill** — chunk + embed for RAG; strategy-routed LLM extraction (chat thread → decisions, meeting transcript → summary, voice → SOP); 12-hourly consolidation of conversations → durable decisions/actions.
3. **Serve** — Next.js chat UI with SSE-streamed answers. BM25 + pgvector + RRF retrieval. Every answer cites. Korean + English.
4. **Notify** — outbound back to the team's substrate (Discord channels v1, Slack/email later). Daily digest, new decisions, regression alerts. Vendor-shared `scripts/notify/` module via `hypeproof-harness`.

## Three architectural laws

These are the non-negotiable constraints; everything else is implementation detail.

1. **Multi-tenant from line 1.** `tenant_id` on every row, Postgres RLS `FORCE` on every table, two DB roles (`curator_app` vs `curator_service`). Boundary principle: no code in `lab_lib/` or `applications/` references a tenant by name. Adding a tenant = config rows, not code edits.
2. **Evidence-grounded answers.** Every chat response includes `[N]` inline citations. Absence = regression caught by E2E + golden recall. Differentiation from any generic chat bot.
3. **Cost discipline.** Cheap model (Haiku) for ingest/distill/router; heavy model (Sonnet) only for chat compose. Per-tenant LLM cost ≤ $5/mo target. Daily cost monitor → `cost.over_budget` notification when over.

## Stack at a glance

| Layer | Tech |
|---|---|
| Backend | FastAPI × 5 services on one Fly VM (NRT), supervised by supervisord |
| Storage | Supabase Postgres 18 + pgvector 0.8 (HNSW cosine), Redis 7 |
| LLM | Anthropic Claude (Haiku + Sonnet), OpenAI embeddings (text-embedding-3-small), Gemini Flash fallback |
| Frontend | Next.js 14 App Router on Vercel, NextAuth GitHub OAuth |
| Capture connectors | Discord (live), GitHub repo (live), Voice/OCR (spec'd P1), Slack/Notion (P2) |
| Validator | Declarative `rubric.yaml` × 5 phases; Playwright E2E; Ralph 50-iter autonomous supervisor |
| CD | GH Actions, path-filtered, Playwright-cached, ~2m38s end-to-end |

## Tenants today

| Slug | Members | Connectors | Status |
|---|---|---|---|
| `hypeproof-lab` | 8 (Jay + 7) | Discord 8 channels @ 30min | Active dogfood; recall@3 27/40 baseline |
| `kids-edu` | 2 admins (Jay, Jinyong) | GitHub 1 repo @ hourly daytime KST | Active; 192 artifacts / 1987 chunks; recall@3 5/10; chat smoke green |
| `acme-test` | 1 placeholder | none | RLS regression coverage only |

## Where the design lives

Read **[`docs/design/README.md`](./docs/design/README.md)** for the master index. Numbered docs cover one functional area each:

| # | Doc | Topic |
|---|---|---|
| 01 | architecture-overview | 3 layers, service topology, repo layout |
| 02 | multitenancy-and-rbac | RLS, tenants/members/integrations, 3-layer RBAC |
| 03 | auth | JWT, dev-token, GitHub OAuth |
| 04 | collection-engine | Connectors, source-kinds, `decide()` |
| 05 | distillation-pipeline | Chunking, embedding, Phase 4 consolidation |
| 06 | retrieval-and-chat | BM25 + pgvector + RRF, LangGraph, SSE |
| 07 | notifications | Outbound — transports, routes, templates |
| 08 | cost-and-observability | Token tracking, daily summary, alerts |
| 09 | validator-harness | Ralph, validator phases, recipes |
| 10 | frontend | Next.js structure, library/members/admin/chat |
| 11 | deployment | Fly + supervisord, Vercel, CD pipeline |
| 12 | source-kinds-catalog | vault/product/harness/transcript/artifacts |
| 13 | tenant-catalog | Per-tenant inventory + onboarding template |

Each follows the same template (Mermaid → component map → data flow → API → config → boundary → coverage). The repetition is intentional — contributors don't switch context jumping between docs.

## ADRs

Time-stamped decisions append to **[`DECISIONS.md`](./DECISIONS.md)**. Reference by date when citing.

## Quick start

See **[`README.md`](./README.md)** for setup + the validator harness commands.

## Status (2026-05-22)

| | |
|---|---|
| **Phase 0** Scaffolding | ✅ shipped |
| **Phase 1** Read-only index | ✅ shipped (recall@3 baseline locked) |
| **Phase 2** Chat MVP | ✅ shipped (Korean + English, multi-turn, cited) |
| **Phase 3** Ingest automation | ✅ shipped (Discord cron + GitHub repo fetch + APScheduler) |
| **Phase 4** Memory consolidation | ✅ shipped (12-hourly decision/action extraction) |
| **Notifications v1** | ⏳ design locked (`docs/design/07`), awaiting Discord webhook URLs |
| **Voice / OCR (Phase A)** | ⏳ spec'd (`docs/design/voice-ocr-connector-spec.md`) |
| **Slack connector** | ❌ Phase 2 |
| **Admin onboarding wizard** | ❌ v2 |
| **First paying tenant** | ❌ planned Q3 2026 |

---

*Version 1.0 — superseded the 1083-line v0.2 draft on 2026-05-22. Detailed design moved to `docs/design/`.*
