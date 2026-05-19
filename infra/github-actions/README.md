# P1-L1 — Vault freshness pipeline

**Goal (the concrete 5/31 sub-target):** a doc merged to `main` is answerable
by Sediment within minutes, and the staleness is *visible*, not silent.

## What's built (in this repo, code — done)

| Piece | Where |
|---|---|
| `POST /webhook/ingest` — HMAC-verified, filters via canonical `_is_excluded`/`_detect_type`, idempotent upsert-by-ref, deletes removed files | `applications/vault_ingester/main.py` |
| `GET /v1/vault/freshness` — last ingest ts + age + `stale` flag | same |
| `vault.ingest` audit event (freshness breadcrumb) | written by the webhook |
| GitHub Action that diffs a push and POSTs changed `.md` with content | `infra/github-actions/vault-ingest.yml` |

nginx already routes `/webhook/` → ingester (`infra/deploy/nginx.conf`), so no
nginx change is needed.

## Gated steps (config / deploy — Jay drives)

1. **Install the workflow** into `jayleekr/hypeprooflab` at
   `.github/workflows/vault-ingest.yml` (that repo holds the vault content;
   the product repo does not).
2. **Repo secrets** on `hypeprooflab`: `SEDIMENT_INGEST_URL`
   (`https://hypeproof-sediment.fly.dev`), `SEDIMENT_WEBHOOK_SECRET`.
3. **Fly secret**: `fly secrets set GITHUB_WEBHOOK_SECRET=<same value>`
   (must equal `SEDIMENT_WEBHOOK_SECRET`).
4. **Redeploy Fly** so the new ingester endpoint exists
   (`fly deploy --config infra/deploy/fly.toml`).

## Verify (after deploy)

```bash
# freshness before any push (expect last_ingest_ts: null, stale: true)
curl -s $URL/v1/vault/freshness | jq

# merge a trivial doc change to hypeprooflab main → Action runs →
curl -s $URL/v1/vault/freshness | jq   # seconds_ago small, stale:false
# then ask Sediment about that doc — it should answer with a citation
```

## Not in L1 (follow-ups, see NEXT.md P1)

- **L2** `consolidate_memory` cron on prod (self-reinforcing memory).
- **L3** Discord ingest (`#daily-research`, `#인사이트-공유`).
- UI badge reading `/v1/vault/freshness` ("updated N min ago") — small
  frontend add, lands with the standalone-frontend deploy.
