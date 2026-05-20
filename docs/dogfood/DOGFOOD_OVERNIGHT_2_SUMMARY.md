# Overnight-2 summary (2026-05-20) — read this first

> "다음 할일 뭔데?" → "자율진행 내일확인할게". Autonomous run; nothing
> needed from you tonight. Read order: this → state JSON → commit diffs.

## What I did (all green, all pushed to origin/main)

| Phase | Result |
|---|---|
| **A. Live smoke of prod** | ✅ ALL routes 200/correct: dev-token, conversations, library (35KB real data), members, vault/freshness, whoami, cite-export(+enum 400), frontend `/sediment`+library+members+admin+pricing+`/api/auth/providers`, full SSE stream with RAG citations, user+assistant message persistence. **The standalone product is genuinely usable.** |
| **D. Fly root → 302** | ✅ `https://hypeproof-sediment.fly.dev/` (+`/anything`) → 302 → `https://sediment-hype-proof-lab.vercel.app/...`. No more "/sediment 404" confusion (NEXT.md P4). API paths untouched (more-specific prefixes match first). Commit `ef7114d`. |
| **E. Discord-ingest webhook** | ✅ `POST /webhook/discord-ingest` LIVE on Sediment side. HMAC reuses `GITHUB_WEBHOOK_SECRET` (single rotation surface). Idempotent (id dedup + fingerprint fallback). Allow-list (only `#weekly`/`#daily-research`/`#인사이트-공유`/`#content-pipeline`; `#잡담` dropped per §8). **7/7 acceptance pass** (id dedup, fingerprint dedup, allow-list reject, empty content, bad sig 401). Commit `1caf9c2`. Mother-side fetch is documented (`docs/dogfood/discord-ingest-mother-contract.md`) — that side is Jay/Restruct territory. |

## What you'd get out of one small action each

These three remain genuinely gated on you. Each is ~30s of input → I take it the rest of the way:

1. **HypeProof Anthropic key** → console.anthropic.com → I `fly secrets set ANTHROPIC_API_KEY=…` → **distill.py actually runs** (currently honest-skips because Sonatus's borrowed key is NEXT.md-flagged as unauthorized for prod). Without this, the engine is *standing but not turning*.
2. **Supabase project** (1 click create) → I do schema apply + 563-doc re-ingest + DATABASE_URL swap → **BM25 → vector retrieval** (NEXT.md "biggest quality gap"). Paraphrased questions stop missing.
3. **DNS for `sediment.hypeproof-ai.xyz`** (one CNAME) → I add the Vercel domain + repoint env → cosmetic but matters for external demo.

## What's running and what's queued

- **Live now**: SSO via GitHub (`https://sediment-hype-proof-lab.vercel.app/sediment`), Fly backend (auto-seed on every deploy), vault-ingest pipeline (any .md merged to hypeprooflab → ~minutes → answerable), root 302, discord-ingest webhook (waiting on Mother).
- **Pre-built, awaiting Mother**: distill "정리" agent (waits for Anthropic key + Discord events); dogfood digest (Mother-posts content for `#sediment-dogfood`).
- **Out of scope**: Restruct (your separate session, already in progress per `96c53e1` harness PR); Studio (no access); pgvector (gated on Supabase create).

## File map

- `docs/dogfood/discord-ingest-mother-contract.md` — what Mother POSTs, signing, dedup, behaviour.
- `docs/dogfood/OVERNIGHT_2_STATE.json` — machine-readable run record.
- `infra/deploy/nginx.conf` — root → 302 (added comment explaining why).
- `services/sediment/applications/vault_ingester/main.py` — new `POST /webhook/discord-ingest`.

A scheduled fallback wakeup may fire tonight — it reads `OVERNIGHT_2_STATE.json` (`status=done`) and no-ops. Nothing to stop.
