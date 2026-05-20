# Mother → Sediment Discord capture contract

> Status (2026-05-20): **Sediment endpoint shipped** (`POST /webhook/discord-ingest`).
> Mother-side fetch + POST = **not in this repo** (hypeprooflab / Mother bot
> — Jay's domain or Restruct session). When that lands, capture is live.

## The endpoint

`POST https://hypeproof-sediment.fly.dev/webhook/discord-ingest`

Headers:
- `Content-Type: application/json`
- `X-Hub-Signature-256: sha256=<hex>` — HMAC-SHA256(body, `$GITHUB_WEBHOOK_SECRET`).
  Reuses the *same* secret as `vault-ingest.yml` (single rotation surface).

Body:

```json
{
  "messages": [
    {
      "id": "1234567890",                // Discord snowflake; STRONG dedup key
      "kind": "message",                 // or "reaction" / "thread", etc.
      "channel": "weekly",               // name without '#'
      "channel_id": "1463019…",          // Discord channel id
      "author_id": "1186944…",
      "author_name": "Jay",
      "content": "AI Curator MVP scaffolding 시작 …",
      "ts": "2026-05-20T01:23:45Z"       // ISO 8601; omit → server-side now()
    }
  ]
}
```

## Behaviour

- **Channel allow-list:** only `weekly`, `daily-research`, `인사이트-공유`,
  `content-pipeline` enter the vault. `#잡담` and any other channel are
  silently dropped (ACTIVATION_ENGINE §8). Empty `content` is dropped.
- **Idempotent:**
  - With `id` → dedup on `payload->>'id'` (strongest, recommended).
  - Without `id` → server computes a fingerprint
    `<channel_id>|<ts>|<author_id>|<content[:200]>` and dedups on that.
- **Rows land in** `events(tenant_id, source='discord', kind, payload, ts)`.
  `distill.py` reads these (`source='discord'`, channels in allow-list) and
  turns commitment-grade content into vault artifacts.
- **Response:** `200 {"ok":true,"inserted":N,"skipped":M}`. Bad signature
  → 401. Bad JSON → 400.

## Mother-side TODO (whoever does Restruct / Mother work)

1. Add (or wire) a periodic Discord fetch in Mother for the 4 allow-listed
   channels. Honor "since-last-fetch" cursor per channel.
2. Batch up to ~50 messages per POST.
3. Sign body with HMAC-SHA256, header `X-Hub-Signature-256: sha256=…`.
4. POST to the endpoint above. Retry transient 5xx with backoff. On 401 →
   rotate the shared secret (it's wrong). On 4xx other than 401 → log + drop
   that batch (don't retry forever — likely a Discord schema surprise).
5. Run on a schedule (e.g., every 10 min — matches `vault.ingest`
   freshness expectations) OR on Discord event push.

## Why not do the Discord fetch from Sediment directly?

The architecture rule is single-owner Discord: **Mother owns send + fetch**
(project CLAUDE.md). Sediment owns ingest + vault. Adding a Discord client
to Sediment would (a) duplicate Mother's auth surface, (b) require an extra
bot token in Fly secrets, (c) blur the responsibility boundary. The webhook
is the on-architecture seam: Sediment exposes "give me decisions/messages",
Mother decides what to send.

## Verification (after Mother integration lands)

```bash
# A single signed POST as a smoke (uses the existing GITHUB_WEBHOOK_SECRET):
SECRET=<get from `fly secrets list` → not readable; use the value set earlier>
BODY='{"messages":[{"id":"smoke-1","channel":"weekly","author_name":"smoke","content":"hello"}]}'
SIG=sha256=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | sed 's/^.*= //')
curl -sS -X POST https://hypeproof-sediment.fly.dev/webhook/discord-ingest \
  -H "Content-Type: application/json" -H "X-Hub-Signature-256: $SIG" -d "$BODY"
# → {"ok":true,"inserted":1,"skipped":0}
# Re-run → {"ok":true,"inserted":0,"skipped":1}  (dedup works)
```

Then `distill.py --since-hours 1` (with a real ANTHROPIC_API_KEY) will see
the new events and produce decisions in the vault. The full loop closes.
