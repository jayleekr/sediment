# Bootstrapping Sediment from scratch

> Captures the **manual** steps that have to happen on a new environment before `fly deploy` or `make seed` will succeed. None of these are auto-runnable — they each require a credential or a one-shot human action.

This doc was written 2026-05-22 after Jay asked: *"manually 한 작업들이 코드에 있지 않나 확인해봐"* — and the answer was: half of them weren't. The list below now lives in git so the next clean rebuild doesn't lose institutional memory.

---

## 1. The 3-layer fly secret model

The runtime fly app expects three DB URLs with strict privilege separation:

| Secret | Role | Use | RLS |
|---|---|---|---|
| `DATABASE_URL_APP` | `sediment_app` | user-facing REST/SSE handlers | **enforced** |
| `DATABASE_URL_SERVICE` | `sediment_service` | cron, workers, ingest, distill | bypassed (defense-in-depth via explicit `WHERE tenant_id`) |
| `SEDIMENT_MIGRATIONS_DB_URL` | `postgres` (Supabase superuser) | one-time DDL (`seed_lab.py` retention columns, future migrations) | bypassed |

This split is the fix for sediment#16's cross-tenant leak. Using the `postgres` superuser as `DATABASE_URL_APP` (the pre-2026-05-21 state) silently disabled all RLS policies because superusers bypass them.

`DATABASE_URL` (legacy single URL) still exists for tools that haven't migrated yet (e.g. the reembed script reads it). It typically points at `postgres` — keep it for backwards-compat but treat new code as if only the 3-tier URLs exist.

## 2. One-time per Supabase project

Run **once** when provisioning a new Supabase project:

```bash
# 1) Open Supabase Dashboard → SQL Editor → paste contents of:
#    infra/supabase-bootstrap.sql
# (Idempotent — re-running on an already-bootstrapped project is a no-op)

# 2) Set role passwords (random 24+ char strings, not in git)
#    Either in the SQL editor:
ALTER ROLE sediment_app     PASSWORD '<random>';
ALTER ROLE sediment_service PASSWORD '<random>';

# 3) Apply the schema migrations
#    The legacy init.sql is for the local docker-compose Postgres.
#    On Supabase, the schema was loaded historically via that same file
#    run-once by the previous owner. For a fresh project, you'd port
#    init.sql to a Supabase-compatible migration (skip the CREATE ROLE
#    lines — those are now in supabase-bootstrap.sql).
```

## 3. One-time per fly app (per environment)

Set these secrets ONCE per fly app (`hypeproof-sediment`, future staging app, etc.):

```bash
# DB tier (from §1):
fly secrets set \
  DATABASE_URL_APP="postgresql+asyncpg://sediment_app:<pwd>@<pooler-host>:5432/postgres" \
  DATABASE_URL_SERVICE="postgresql+asyncpg://sediment_service:<pwd>@<pooler-host>:5432/postgres" \
  SEDIMENT_MIGRATIONS_DB_URL="postgresql://postgres:<superuser-pwd>@<pooler-host>:5432/postgres" \
  DATABASE_URL="postgresql://postgres:<superuser-pwd>@<pooler-host>:5432/postgres" \
  -a hypeproof-sediment

# LLM + embeddings:
fly secrets set \
  GEMINI_API_KEY=<key> \
  ANTHROPIC_API_KEY=<key> \
  EMBEDDING_PROVIDER=gemini \
  -a hypeproof-sediment

# Auth:
fly secrets set JWT_SECRET="<random-32-byte>" -a hypeproof-sediment

# Discord (collection + notifications):
fly secrets set \
  DISCORD_BOT_TOKEN=<bot-token> \
  DISCORD_WEBHOOK_SEDIMENT=<url> \
  DISCORD_WEBHOOK_HYPEPROOF_STUDIO=<url> \
  DISCORD_WEBHOOK_HYPEPROOFLAB_PAGE=<url> \
  DISCORD_WEBHOOK_MEETING_NOTES=<url> \
  DISCORD_WEBHOOK_MANAGER_NOTICES=<url> \
  -a hypeproof-sediment

# GitHub (collection from public + private repos):
fly secrets set GITHUB_TOKEN=<personal-or-app-token> -a hypeproof-sediment

# (Optional) OpenAI fallback embedding provider:
# fly secrets set OPENAI_API_KEY=<key> -a hypeproof-sediment
```

## 4. Discord webhooks — created via bot API

The 5 `DISCORD_WEBHOOK_*` values above are PER-CHANNEL webhook URLs. They were created via the Discord bot REST API (not manually clicked through Discord settings) so each can be programmatically rotated.

Recipe (one-shot, requires `DISCORD_BOT_TOKEN` to have `Manage Webhooks` on the target channels):

```bash
# Example for the #sediment channel
CHANNEL_ID=1506104152747671694
curl -X POST -H "Authorization: Bot $DISCORD_BOT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Sediment"}' \
  "https://discord.com/api/v10/channels/$CHANNEL_ID/webhooks"
# → response has .url — that's what goes into DISCORD_WEBHOOK_SEDIMENT
```

Repeat per channel (sediment, hypeproof-studio, hypeprooflab-page, meeting-notes, manager-notices).

## 5. GitHub repos that aren't this monorepo

The CLI distribution depends on TWO sibling repos that have to exist before the CLI release workflow can publish:

| Repo | Purpose | Bootstrap |
|---|---|---|
| `jayleekr/sediment-cli-releases` | Public mirror for CLI binaries (so `brew` and `curl` install without GH auth) | `gh repo create jayleekr/sediment-cli-releases --public --license MIT --description "Public release mirror for the Sediment CLI"` — then push the `README.md` + `install.sh` from `docs/integration/from-openclaw.md`'s sibling files |
| `jayleekr/homebrew-sediment` | Homebrew tap formula | `gh repo create jayleekr/homebrew-sediment --public --license MIT` — then commit `Formula/sediment.rb` |

And ONE GitHub PAT secret on this repo:

```bash
# Set on the source (private) repo to enable auto-mirror on tag push.
# Fine-grained PAT: Repository=sediment-cli-releases, Contents=R+W only.
gh secret set RELEASE_MIRROR_TOKEN --repo jayleekr/sediment --body 'github_pat_...'
```

See sediment#33 for the full design.

## 6. Vault content (per tenant)

Sediment doesn't store the source-of-truth vault content; it ingests it. Each tenant's vault is a separate repo, registered via the `integrations` table at runtime:

```sql
INSERT INTO integrations (tenant_id, kind, config) VALUES (
  '<tenant-uuid>', 'github',
  '{"repo": "JinyongShin/hypeproof_kids_edu", "branch": "main",
    "include_globs": ["wiki/**/*.md"]}'
);
```

Then the scheduler's `github_repo_sync` cron pulls it on the configured interval. First sync seeds chunks + embeddings; subsequent syncs are incremental by SHA watermark (see `scripts/github_repo_fetch.py`).

## 7. Manually-applied SQL not in any seed file

Operations that were one-shot in a specific environment and have NOT been backfilled into seed/migration code:

| When | What | Code location now |
|---|---|---|
| 2026-05-21 | Created `sediment_app` / `sediment_service` roles, dropped legacy `curator_app` / `curator_service` | `infra/supabase-bootstrap.sql` ← NEW (this PR) |
| 2026-05-22 | Manually applied retention columns to `conversations` via superuser conn | `scripts/seed_lab.py::ensure_retention_columns` — runs on every deploy if `SEDIMENT_MIGRATIONS_DB_URL` is set |
| Earlier | Stale artifact cleanup (`research/daily/*` orphans after vault path migration) | `scripts/cleanup_stale_artifacts.py` (one-shot, not on cron) |
| 2026-05-22 | Test-conversation pollution cleanup | `scripts/cleanup_test_conversations.py` (one-shot, run once per env) |

If you're rebuilding from scratch, run them in this order:
1. `infra/supabase-bootstrap.sql` via Supabase SQL Editor
2. Apply existing init.sql DDL (tables + indexes + RLS policies)
3. `fly secrets set …` per §3
4. `fly deploy` — release_command runs seed_lab which adds any missing columns + seeds tenants/members
5. Register integrations (§6)
6. First scheduled sync populates chunks; first user creates first conv

## 8. Cron-style state to verify after a clean rebuild

- `select count(*) from chunks where embedding = (array of 1536 zeros)::vector` should be 0 once reembed completes
- `select kind, count(*) from events group by 1` should show the connector kinds you registered
- Sidebar in the web UI should show no `kids-edu-smoke` / `probe` / etc. — `scripts/cleanup_test_conversations.py` is the scrub
- `gh secret list --repo jayleekr/sediment | grep RELEASE_MIRROR` confirms the mirror token is set

## 9. Things that ARE in code (for reference)

So you know what's reproducible from git alone:

- All tables, indexes, RLS policies — `infra/init.sql`
- Idempotent column additions — `scripts/seed_lab.py`
- All cron schedules — `config/cron.yaml`
- All notification templates + routes — `scripts/notify/`
- All API contracts — `applications/sediment_*` (FastAPI routers)
- Frontend — `frontend/` (Next.js, deployed via Vercel; secrets in Vercel UI)
- CLI — `services/sediment-cli/` (Rust, released via `.github/workflows/sediment-cli-release.yml`)

## Changelog
- 2026-05-22 — v0.1 — first capture after Jay's audit prompt.
