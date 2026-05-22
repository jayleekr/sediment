# Runbook — Supabase Pro Upgrade

**Status:** Required before sustained production cron (auto-pause kills cron jobs).

**Owner:** Jay (only org owner has Pro upgrade button).

**Time required:** 5 minutes.

**Reversibility:** Yes — can downgrade anytime; data is preserved.

## Why upgrade

| Free tier (current) | Pro tier ($25/mo) |
|---|---|
| ⚠️ Auto-pause after 7 days inactivity | ✅ Never pauses |
| 60 simultaneous connections | 200+ |
| 500 MB database | 8 GB included, $0.125/GB after |
| 1 GB file storage | 100 GB included |
| ❌ No daily backups | ✅ 7-day point-in-time recovery |
| ❌ No support SLA | ✅ Email support |

Our current DB usage: **92 MB** (well under 8 GB cap). Auto-pause is the
real driver — once scheduler runs every 30 minutes, the project won't
pause from inactivity. But a single 8h outage (e.g. Fly restart marathon)
+ Sunday low activity = could still trigger the 7-day timer in a
deceiving way. Pro removes the variable entirely.

## Pre-upgrade checklist

```bash
# 1. Verify current DB size — should be << 8 GB
# Use fly-exec.sh (wraps `fly machine exec`) — `fly ssh console -C` hangs
# non-interactively, see sediment#54.
bash harness/scripts/fly-exec.sh "/run-with-db.sh python -c \"
import asyncio
from sqlalchemy import text
from lab_lib.db import service_session
async def main():
    async with service_session() as s:
        r = await s.execute(text(\\\"SELECT pg_size_pretty(pg_database_size(current_database()))\\\"))
        print('Current DB size:', r.scalar())
asyncio.run(main())
\""
# Expected: ~100 MB. If > 6 GB, plan for storage cost.

# 2. Verify connection pooling URL is being used (not direct connect)
grep DATABASE_URL ~/.env | grep -oE 'pooler|aws-1-' && echo "✓ pooler" || echo "✗ direct"
```

## Upgrade steps (Jay does this)

1. Go to https://supabase.com/dashboard/project/etmdeixjzstwhoqrgxfo/settings/general
2. Click **"Upgrade to Pro"** (top right of project)
3. Select billing plan (Pro $25/mo)
4. Pay (need a card on file)
5. ✅ Upgrade takes ~30 seconds, no downtime
6. Verify: dashboard top-right badge changes from "Free" to "Pro"

## Post-upgrade verification

```bash
# Connection should keep working unchanged
bash harness/scripts/fly-exec.sh "/run-with-db.sh python -c \"
import asyncio
from sqlalchemy import text
from lab_lib.db import service_session
async def main():
    async with service_session() as s:
        r = await s.execute(text('SELECT version()'))
        print(r.scalar())
asyncio.run(main())
\""

# Check connection count limit (Pro: 200, Free: 60)
# (Done in Supabase dashboard → Database → Pooler stats)
```

## What changes about our code

**Nothing.** Same URL, same credentials, same library. Pro is a billing/
quota change, not a different product.

## What changes about our cron

- ✅ No more auto-pause risk → cron jobs reliably hit DB
- ✅ Higher connection limit → simultaneous fetch + distill safe
- ✅ Point-in-time recovery → can roll back accidental mass deletes
- ⚠️ Be aware: each scheduled job currently uses ~3-5 connections briefly.
  At 8 channels × 30-min fetch + hourly distill, peak connection usage
  is ~10-15 simultaneous. Pro tier handles this comfortably.

## Downgrade if needed

- Project → Settings → Billing → Downgrade to Free
- Existing data preserved
- ⚠️ DB > 500 MB will block downgrade — must shrink first.

## Trigger / decision criteria

| When to upgrade | Status |
|---|---|
| Before starting production cron (this week) | 🟡 NOW |
| Before first external tenant demo | ✅ definitely |
| Before paid SaaS launch | ✅ definitely |
| For dogfood-only with manual triggers | 🟢 not required |
