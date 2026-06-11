# Sediment — Fly.io Deployment Runbook

Phase 1 target: **single Fly app + Fly Postgres**, public URL
`sediment.hypeproof-ai.xyz`. Cost ceiling: ~$5/mo (free tier should cover
idle hours; Postgres tier dominates).

---

## What you need before starting

1. **Fly.io account** — sign up at https://fly.io. Card on file required even
   for free tier.
2. **flyctl** locally — `brew install flyctl` then `fly auth login`.
3. **HypeProof-dedicated Anthropic API key** — DO NOT reuse the Sonatus
   `SNT_CLAUDE_API_KEY`. Issue a new key at
   https://console.anthropic.com/settings/keys with a $5 hard cap.
4. **DNS** — access to `hypeproof-ai.xyz` DNS records (Cloudflare/Namecheap).

---

## One-shot deploy (first time)

```bash
cd products/sediment

# 1. Create the app
fly apps create hypeproof-sediment --org personal

# 2. Provision Postgres (free tier: 1GB, single node)
fly postgres create \
  --name hypeproof-sediment-db \
  --region nrt \
  --vm-size shared-cpu-1x \
  --volume-size 1 \
  --initial-cluster-size 1
# Save the connection string it prints — it's NOT recoverable.

# 3. Attach Postgres to the app (sets DATABASE_URL automatically)
fly postgres attach hypeproof-sediment-db --app hypeproof-sediment

# 4. Set secrets
fly secrets set --app hypeproof-sediment \
  JWT_SECRET="$(openssl rand -hex 32)" \
  ANTHROPIC_API_KEY="sk-ant-..." \
  GITHUB_WEBHOOK_SECRET="$(openssl rand -hex 16)"

# 5. Deploy
fly deploy --config infra/deploy/fly.toml --dockerfile Dockerfile

# 6. Run migrations + seed (one-time)
# fly-exec.sh wraps `fly machine exec` (doesn't hang like `fly ssh console -C`,
# see sediment#54).
bash harness/scripts/fly-exec.sh \
  "cd /app/services/sediment && python -m scripts.migrate_lab && python -m scripts.seed_lab"

# 7. Verify
curl https://hypeproof-sediment.fly.dev/healthz
# → {"status":"ok","service":"sediment-platform"}
```

---

## Custom domain (sediment.hypeproof-ai.xyz)

```bash
# 1. Tell Fly about the domain
fly certs add sediment.hypeproof-ai.xyz --app hypeproof-sediment

# 2. Fly will print 2 DNS records to add. Typical:
#    CNAME sediment.hypeproof-ai.xyz → hypeproof-sediment.fly.dev
#    A     sediment.hypeproof-ai.xyz → <Fly IP>   (if CNAME-at-apex not allowed)

# 3. After DNS propagates (~5-15 min):
fly certs check sediment.hypeproof-ai.xyz --app hypeproof-sediment
# → expect status=Ready
```

---

## GitHub webhook (auto-ingest on push)

Once the app is up:

```
GitHub → repo settings → Webhooks → Add webhook
  Payload URL:  https://sediment.hypeproof-ai.xyz/webhook/github
  Content type: application/json
  Secret:       <value of GITHUB_WEBHOOK_SECRET>
  Events:       Just push
```

Verify: push a trivial commit, then within 30s:
```bash
curl https://sediment.hypeproof-ai.xyz/api/v1/library?limit=3 \
  -H "Authorization: Bearer $TOKEN"
```

---

## MCP server pointed at production

Once deployed, run the connect skill against the public URL:

```bash
# From any laptop in the team
SEDIMENT_BASE_URL=https://sediment.hypeproof-ai.xyz \
SEDIMENT_LG_BASE=https://sediment.hypeproof-ai.xyz \
  /sediment-connect <your@email>
```

The MCP config in `~/.claude/mcp_servers/sediment.json` will point at the
production URL. Restart Claude Code → `sediment__*` tools available in every
worktree on that laptop.

---

## Cost watch

| Component | Free tier | Paid (if exceeded) |
|---|---|---|
| App VM (shared-cpu-1x, 512MB, scale-to-zero) | covered | $0 + $0.0000022/sec running |
| Postgres (1 vCPU, 1GB RAM, 1GB disk) | covered | $5/mo |
| Bandwidth | 160GB/mo | $0.02/GB after |
| Anthropic API | n/a | hard cap $5/mo recommended |

Expected steady state: $5/mo (Postgres) + ~$2/mo (Anthropic at ~100
queries/day with Sonnet).

---

## Rollback

```bash
fly releases --app hypeproof-sediment     # list
fly releases rollback <N> --app hypeproof-sediment
```

Postgres is NOT rolled back. Schema migrations are forward-only; if a
migration goes bad, restore from Fly Postgres snapshot (taken daily on free
tier).

---

## Local Docker test (before pushing to Fly)

```bash
cd products/sediment

# Build
docker build -t sediment:local -f Dockerfile .

# Run against an existing local postgres
docker run --rm -p 8080:8080 \
  -e DATABASE_URL="postgres+asyncpg://sediment:curator_local_dev@host.docker.internal:5433/curator" \
  -e JWT_SECRET="local-test-only" \
  -e LLM_PROVIDER="offline" \
  sediment:local

# In another shell:
curl http://localhost:8080/healthz
curl -X POST http://localhost:8080/api/v1/auth/dev-token \
  -H 'Content-Type: application/json' \
  -d '{"email":"jayleekr0125@gmail.com"}'
```

---

## Known limitations (Phase 1)

- **Single VM** — no horizontal scaling. ~50 concurrent SSE streams ceiling.
- **No Redis** — some background workers degrade gracefully when REDIS_URL
  is unset.
- **No queue** — webhook → ingest is synchronous. Large pushes may hit Fly's
  60s HTTP timeout (mitigation: shallow scan, queue the rest).
- **dev-token still works** — Phase 2 closes this with Discord OAuth.
- **No CDN / static asset hosting** — web/ (Next.js) is deployed separately
  to Vercel.

Phase 2 unlocks: Discord OAuth, Redis cache, background ingest queue,
multi-instance scaling.
