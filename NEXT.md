# Sediment — NEXT (post-MVP roadmap)

> Status: **MVP is LIVE + first public reveal** as of 2026-05-20.
> UI → https://sediment.hypeproof-ai.xyz/sediment (standalone Vercel)
> API → https://hypeproof-sediment.fly.dev (Fly, API-only; `/` 302→UI)
> Old URL → https://web-nu-seven-39.vercel.app/sediment (307→new domain via hypeprooflab#56)
>
> Pipeline: every main push → auto `fly deploy` (GHA `fly-deploy.yml`) →
> post-deploy E2E-12 prod smoke against the live URL. No more manual deploys.
>
> This file lists the big remaining work, in priority order. Each item is
> sized to be picked up cold in a fresh session. See the
> `sediment-mvp-live` memory for live infra facts (Fly app, PG, keys, model)
> and `EXTRACTION.md` for the repo-split provenance.

---

## P0 — Frontend standalone cutover  *(functionally complete 2026-05-20)*

**Done:**
1. ✅ Standalone Next.js 16 app scaffolded under `frontend/` (build green, 9 routes)
2. ✅ New Vercel project deploying `frontend/` at custom domain
   `sediment.hypeproof-ai.xyz` with env vars pointing at the Fly API
3. ✅ Smoke verified live — 6 public routes 200, Fly API CORS allows new
   origin, env-aware badge reads "prod"
4. ✅ Old URL `web-nu-seven-39.vercel.app/sediment` → 307 → new domain
   (hypeprooflab#56 merged) — dogfood bookmarks keep working
5. ✅ Dogfood team announced (5/20 Discord #sediment first reveal)

**Remaining (small, separate issues will be filed):**
- After ~3 days soak: flip hypeprooflab redirect from 307 → 308
- After 308 stable: delete `web/src/app/sediment/` from `hypeprooflab`
  (dead code once redirect ships permanently)

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

> **↳ 06-04 reconcile** — verified against the local repo, not assumed (one remote claim was wrong):
> - **L1 is BUILT — not "NOT built".** A public, HMAC-verified ingest webhook exists:
>   `POST /webhook/ingest` (`services/sediment/applications/vault_ingester/main.py:239`, verifies
>   `X-Hub-Signature-256` against `GITHUB_WEBHOOK_SECRET`), fed by the GitHub Action
>   `.github/workflows/vault-ingest.yml`; nginx publicly proxies `/webhook/` → ingester
>   (`infra/deploy/nginx.conf:110`, upstream `127.0.0.1:11000`). A `/webhook/discord-ingest` sibling
>   exists too. The old "ingester only exposes `/v1/ingest/*`, not publicly routed" note is stale.
> - **A poll path ALSO exists** (belt-and-suspenders): `github_repo_sync` (`enabled: true`,
>   `0 0-13 * * *`, hourly 09–22 KST) — `services/sediment/config/cron.yaml:60` — pulls md
>   revisions for every tenant with an `integrations` kind='github' row.
> - **L2 IS scheduled on prod — not "not scheduled".** The `consolidate` job (Phase 4 memory
>   consolidator) runs `15 */12 * * *` (09:15 + 21:15 KST, tenant hypeproof-lab) — `cron.yaml:49`;
>   `distill` (`5 * * * *`) is active too. Caveat: only `github_repo_sync` carries a literal
>   `enabled: true`; `consolidate`/`distill` rely on the loader's default-on (no-flag) convention.
> - **Freshness metric is ALSO already built — not "not started".** `GET /api/v1/vault/freshness`
>   (`sediment_platform/routers/vault.py:64`, tenant-scoped, returns `last_ingest_ts`/`seconds_ago`/`stale`
>   + signals/violations) is live on prod (HTTP 401 = route up, auth-gated), and `FreshnessBadge` is
>   mounted in `frontend/app/sediment/layout.tsx:36` rendering "vault Nh ago". Verified 2026-06-04:
>   55 vault/freshness tests pass + ruff clean. So **all of P1's L1 / L2 / freshness are shipped** →
>   TODO.md **T4 = DONE**. The only genuine P1 gap left is operational tuning, not net-new build.
>
> (cron.yaml / vault_ingester / nginx.conf are byte-identical on `origin/main`, so this holds post-rebase.)

**Acceptance:** a doc merged to `main` is answerable by Sediment within ~5 min,
with a visible freshness indicator.

**Do NOT start without Jay's go-ahead** — he explicitly scoped this to its own session.

---

## P2 — Supabase pgvector migration  *(done 2026-05-21)*

**Status:** ✅ live. Fly DATABASE_URL now points at Supabase pooler
(`aws-1-ap-southeast-1.pooler.supabase.com`); 15 tables, pgvector 0.8.0,
HNSW index (`vector_cosine_ops`, m=16, ef_construction=64), GIN tsv index,
RLS on 14 tables. 690 artifacts / 6,469 chunks ingested.

**Measured (2026-05-21):**
- recall@3: 26 PASS / 5 PART / 9 MISS, avg 71.2%
- p50/p95 latency: 866/2809ms
- 5 historical baseline failures (GQ-017/020/024/029/035) all now PASS —
  pgvector + HNSW gives real lift over the BM25-only Fly PG baseline.
- 14 sub-1.0 queries are mostly stale `ideal_refs` (vault grew 563→690 →
  some referenced files no longer in vault). Tracked in #10.

**Fly Postgres `hypeproof-sediment-db`:** machine stopped 2026-05-21
(volume retained as recovery point). After ~1 week of stable Supabase
operation, destroy via:
```
fly machine destroy 7815619b606198 --app hypeproof-sediment-db --force
fly volume destroy vol_v3ggq0qe0gomklx4 --app hypeproof-sediment-db
fly apps destroy hypeproof-sediment-db
```

**Nightly recall check:** `.github/workflows/nightly-recall.yml` runs
`validator.scripts.recall_live` daily at 18:30 UTC (03:30 KST). Fails if
PASS count drops below 20/40 (currently 26). No secrets needed.

> **↳ 06-04 reconcile** — verified:
> - **Fly PG "~1 week stable" destroy precondition is MET.** Machine stopped 2026-05-21;
>   today 2026-06-04 = **14 days** (2× the threshold). The destroy commands above are now
>   actionable → TODO.md **T1** (operator-run only, irreversible — do NOT auto-execute).
> - **"No secrets needed" is now FALSE — the nightly recall check is BROKEN in prod.**
>   `recall_live.py` mints a JWT via `POST /api/v1/auth/dev-token` (`validator/scripts/recall_live.py:52`),
>   but that endpoint is gated by `SEDIMENT_DEV_MODE` and **403s in prod** (`auth.py:53-54`,
>   "dev mode disabled" — a deliberate CVE-class auth-bypass fix; gate is identical on `origin/main`).
>   So `nightly-recall.yml` (runs against `…fly.dev`) fails at token-mint **every** run
>   (`raise_for_status` → exit 1) and fires a misleading "recall regression" Discord alert without
>   ever reaching the queries. `fly-deploy.yml:131-138` already abandoned the same mint for this reason.
>   Fix → TODO.md **T5**: inject a `SEDIMENT_CI_TOKEN` / device-flow auth — **NOT** by re-enabling
>   `SEDIMENT_DEV_MODE` in prod. (`SEDIMENT_EMAIL` default was normalized to the HypeProof gmail on
>   `origin/main` via #60's identity sweep — already correct on the rebased branch.)

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

---

## Operational baseline (added 2026-05-20)

- **CD**: every main push touching runtime paths (allowlist in
  `.github/workflows/fly-deploy.yml`) → auto `fly deploy --remote-only`
  → release_command (idempotent `seed_lab`) → rolling update. ~3–4 min.
- **Smoke**: post-deploy job runs Playwright E2E-12 against
  `sediment.hypeproof-ai.xyz` (no auth, 6 routes, brand assert,
  console-error budget 8). Zero secrets needed. Screenshots upload as
  artifact on failure. Playwright pinned to `1.60.0`, chromium cached.
- **Spec**: `validator/e2e_spec.yaml` v0.2 multi-env. `SEDIMENT_E2E_ENV`
  selects dev (localhost, dev-token auth) or prod (live URL, no auth).
  Flow without `environments:` tag defaults to `[dev]` (backwards compat).
- **Discord ingest**: APScheduler in-VM (`b67b462`) runs every 30 min
  per `services/sediment/config/cron.yaml` (8 channels). Replaces the
  removed laptop-side launchd plists.
- **GH cross-repo**: redirect from hypeprooflab old URL → new domain
  shipped as `hypeprooflab#56` (307, will flip 308 after soak).

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
