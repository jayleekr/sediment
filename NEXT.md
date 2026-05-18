# Sediment — NEXT (post-MVP roadmap)

> Status: **MVP is LIVE and dogfood-ready** as of 2026-05-15.
> UI → https://web-nu-seven-39.vercel.app/sediment (Vercel)
> API → https://hypeproof-sediment.fly.dev (Fly, API-only; `/sediment` 404 is by design)
>
> This file lists the big remaining work, in priority order. Each item is
> sized to be picked up cold in a fresh session. See the
> `sediment-mvp-live` memory for live infra facts (Fly app, PG, keys, model)
> and `EXTRACTION.md` for the repo-split provenance.

---

## P0 — Frontend standalone cutover  *(blocks "clean separation")*

**Why this is P0 now.** This repo was split out of `jayleekr/hypeprooflab`
(2026-05-18). The backend came over cleanly. The **frontend did not** — it
still lives in and deploys from the community Next.js app.

**Current state.** `frontend/app-sediment/` holds the 11 UI files verbatim,
but they're not runnable here: they import shared
components/libs (`CookieConsent`, `web/src/app/sediment/lib/*`, Tailwind
config) that stayed in the community repo. The **live dogfood UI is still
served by hypeprooflab's `web/`** (Vercel `web-nu-seven-39.vercel.app/sediment`),
deliberately left there so an auto-deploy cron doesn't break the team's
dogfood mid-cutover.

**Work:**
1. Scaffold a standalone Next.js app here (own `package.json`, `next.config`,
   Tailwind, the shared components it needs copied/vendored).
2. New Vercel project from this repo → custom domain (P3:
   `sediment.hypeproof-ai.xyz`), `NEXT_PUBLIC_*` → Fly API.
3. Verify E2E (Playwright) against the new deploy.
4. Repoint the dogfood team to the new URL.
5. **Only then** delete `web/src/app/sediment/` from `hypeprooflab` (it is
   intentionally still there until this step — do not remove it earlier).

**Acceptance:** Sediment UI deploys from this repo, no Sediment code remains
in `hypeprooflab`'s working tree, dogfood uninterrupted.

---

## P1 — Dogfood feeding pipeline  *(deferred to a dedicated session by Jay)*

**Problem.** The vault is a **frozen snapshot**: 563 artifacts ingested once
manually (local `ingest_repo` → Fly PG over `fly proxy`). Nothing updates it.
Every doc/decision created after 2026-05-15 is invisible to Sediment, so the
tool goes stale the moment the team starts relying on it.

**Designed (not built) layered strategy:**

| Layer | What | Why it matters | State |
|---|---|---|---|
| L1 | GitHub Action on `push` → POST `/webhook/ingest` (Fly) | Backbone: repo is the SoT, every merge re-ingests changed files | nginx `/webhook/` → ingester already routes; `GITHUB_WEBHOOK_SECRET` already a Fly secret. **The public webhook endpoint itself is NOT built** — ingester only exposes `/v1/ingest/document` + `/v1/ingest/batch`, not publicly routed |
| L2 | Turn on `consolidate_memory` cron on prod | Self-reinforcing: team usage → extracted decisions feed the vault. Most "mirror-loop" / on-brand | `scripts/consolidate_memory.py` exists + Phase 4 validated locally; not scheduled on prod |
| L3 | Discord ingest (`#daily-research`, `#인사이트-공유`) | Captures knowledge that never lands in git | not started |
| — | Freshness metric | "vault last updated N hours ago" surfaced in UI/healthz so staleness is visible, not silent | not started |

**Acceptance:** a doc merged to `main` is answerable by Sediment within ~5 min,
with a visible freshness indicator.

**Do NOT start without Jay's go-ahead** — he explicitly scoped this to its own session.

---

## P2 — pgvector on Fly PG  *(biggest quality lever)*

**Problem.** `flyio/postgres-flex` has no pgvector extension. `init-fly.sql`
was adapted: `embedding` column is `text`, HNSW index dropped. Retrieval is
**BM25-only** — no semantic recall. This is the single biggest quality gap;
paraphrased / conceptual queries that don't share lexical tokens miss.

**Options:**
1. Migrate Fly PG → a pgvector-capable image (custom Docker PG w/ pgvector), restore data.
2. Move PG to Supabase (pgvector built-in, free tier OK for 8 users) — also gets us a managed dashboard.
3. Stay BM25 but add a re-rank pass (cheaper, smaller ceiling).

**Recommendation:** Supabase (least ops, unblocks pgvector + gives a UI).
Re-run schema + 563-artifact ingest against the new PG, repoint `DATABASE_URL`.

**Acceptance:** `recall@3` on `golden_queries.yaml` measurably up vs the
BM25-only baseline (`services/sediment/.venv/bin/python -m validator.checks.regression_rag`).

---

## P3 — Clean domain

`web-nu-seven-39.vercel.app` is an ugly auto-slug. Add a custom domain
(suggested: `sediment.hypeproof-ai.xyz`) in Vercel + DNS, update
`web/.env.production` `NEXT_PUBLIC_CURATOR_*` only if the API host also moves
(it doesn't need to — keep `hypeproof-sediment.fly.dev` or alias it too).

---

## P4 — Fly root → Vercel redirect (kill the 404 dead-end)

Hitting `https://hypeproof-sediment.fly.dev/sediment` returns nginx 404 by
design (Fly is API-only). Jay hit this repeatedly and read it as "broken".
Fix: in `infra/deploy/nginx.conf`, change `location / { return 404; }` to a
302 → the Vercel UI for browser GETs (keep 404 for API paths, or just
unconditional 302 to the UI root). Low effort, removes a recurring confusion.

---

## P5 — Loose ends

- **Simon has no email** in `data/members.json` → can't mint a dev token / log
  in. Add his email and reseed (`scripts/seed_members` or equivalent), then
  re-run the members ingest so he's both an auth subject and a vault citation.
- **Anthropic key is the SNT key (temporary).** The Phase 4
  `consolidate_memory` worker runs on Anthropic Haiku via `ANTHROPIC_API_KEY`,
  currently the Sonatus key (authorized for testing only). Provision
  HypeProof's own Anthropic key and `fly secrets set` it before P1/L2 turns
  the worker on in prod.
- **gemini-2.5-pro is unusable in prod** (~100% 503, ~60s latency). Chat is
  pinned to `gemini-2.5-flash` (fast, grounded). Revisit pro only if Google
  fixes availability and answer quality on flash proves insufficient.
- **Branch not on main yet** — MVP work lives on
  `ai/coder/p2-sec-05-20260513T105526`. (Being merged in this session.)

---

## Reference — how the MVP was stood up (so a redo is reproducible)

1. Fly app `hypeproof-sediment` (nrt), Fly PG cluster `hypeproof-sediment-db`.
2. Schema: adapted `infra/init.sql` → `/tmp/init-fly.sql` (no pgvector:
   `embedding text`, drop HNSW index, DB name `hypeproof_sediment`), applied
   as the `postgres` superuser over `fly proxy`.
3. App role `hypeproof_sediment`: `GRANT ALL` on all tables + `ALTER ROLE …
   BYPASSRLS` (single-tenant dogfood — acceptable).
4. `DATABASE_URL` Fly secret set manually with `?ssl=disable` (asyncpg rejects
   libpq `sslmode`; `start.sh` now auto-converts for future `fly postgres attach`).
5. Vault ingest: local `ingest_repo` over `fly proxy 5432`. Frontmatter
   `date` strings coerced to `datetime.date` in the ingester (asyncpg rejects
   `str` for a DATE column). Final: 563 OK / 0 fail.
6. CORS: `allow_origin_regex` for `*.vercel.app` / `hypeproof-ai.xyz` /
   `hypeproof.studio` on both platform + langgraph.
7. Single machine (`fly scale count 1`), always-on, VM 1024MB,
   `uvicorn --workers 2` for platform + langgraph. `/healthz` answered by
   nginx locally (not proxied — proxying starved it during streaming).
8. Frontend: `web/.env.production` pins `NEXT_PUBLIC_CURATOR_*` → Fly;
   `cd web && vercel --prod --yes`.
9. Verified: Playwright E2E (sign-in → query → Gemini answer → citation modal),
   3 sequential chats 4/6/6s, no brand leak ("AI Curator" absent from DOM).
