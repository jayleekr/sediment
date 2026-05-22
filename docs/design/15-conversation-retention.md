# 15 — Conversation Retention

> **One-line:** Conversations are *raw events* in Sediment's terms — they exist to feed Phase 4 distillation into durable `decisions`/`actions`. After 30 days they auto-archive (hidden), after 90 days they soft-delete. User can `pin` important threads (permanent) or open them as `temporary` (zero retention). Test pollution is hidden by title-prefix filter.

## 1. Why this doc exists

Earlier the system stored every conversation forever:
- Sidebar showed 20+ entries, most of them test pollution (`kids-edu-smoke`, `probe`, my own smoke runs)
- No GC, no archive, no retention concept
- Storage grows unbounded
- PIPA exposure: a user momentarily typing personal info gets it stored permanently

Jay's reframe: **"Does chat need to be stored at all?"** Honest answer: **partially.** Multi-turn anaphora needs short-term retention; Phase 4 needs long enough to extract; permanent storage of every Q&A is overkill.

This doc fixes that.

## 2. Sediment philosophy alignment

Sediment's value unit hierarchy:

```
artifacts + decisions + actions   ← durable knowledge (forever)
       ▲
       │  extracted by Phase 4 consolidator from
       │
conversations + messages          ← raw events (transient)
       ▲
       │  produced by
       │
human interaction (chat, CLI, MCP)
```

**Conversations are scaffolding for extraction.** Once Phase 4 has mined decisions/actions, the raw conv is mostly dead weight. Keeping it forever doesn't add to "doing → knowing"; the knowing is now in `decisions`. Keeping it for a window helps:
- Multi-turn context (anaphora resolution within a session)
- Audit / "who asked what" investigation
- Improvement signal (which queries succeeded/failed)
- User scroll-back ("what did I ask yesterday?")

After that window: gone.

## 3. The policy

```
┌─────────────────────────────────────────────────────────────┐
│  ACTIVE      0–30 days     visible in sidebar              │
│              │              search-indexed                   │
│              │              Phase 4 reads it                  │
│              ▼                                                │
│  ARCHIVED   30–90 days     hidden from sidebar (default)    │
│              │              still queryable with ?archived=1 │
│              │              decisions/actions already extracted │
│              ▼                                                │
│  DELETED    > 90 days      conv row + messages physically gone │
│                            decisions/actions REMAIN — they're │
│                            durable; their conv_id back-link    │
│                            becomes NULL (orphan citation OK)  │
└─────────────────────────────────────────────────────────────┘
```

**Overrides:**
- **`pinned = true`** → never archived, never deleted. User opts in via UI star/pin button. Use sparingly — for design discussions, founder-level threads.
- **`temporary = true`** → archived immediately on completion (or 1 hour after last message), deleted 24h after. Use for sensitive one-off queries ("회사 매출 보여줘"). User opts in at conversation creation.

## 4. Schema

```sql
ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS pinned        BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS archived_at   TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS temporary     BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS purge_after   TIMESTAMPTZ;     -- precomputed at creation/pin change

CREATE INDEX IF NOT EXISTS conversations_archived_at_idx ON conversations (archived_at);
CREATE INDEX IF NOT EXISTS conversations_purge_after_idx ON conversations (purge_after);
```

`purge_after` is a computed column maintained by trigger or update — `(temporary AND created_at + 24h) OR (NOT pinned AND created_at + 90d)`. NULL if pinned. The retention cron scans `WHERE purge_after < now()`.

## 5. Cron jobs

```yaml
# config/cron.yaml
retention_sweep:
  enabled: true
  schedule: "0 4 * * *"        # 04:00 UTC = 13:00 KST (off-peak)
  steps:
    - archive_inactive_30d     # set archived_at = now() WHERE archived_at IS NULL AND created_at < now() - 30d AND NOT pinned
    - purge_expired            # DELETE FROM conversations WHERE purge_after < now()
    - log_summary              # how many archived, how many deleted, how many pinned
```

Idempotent. Skipping a day or running twice is harmless.

## 6. UI exposure

| Action | UI element | Status |
|---|---|---|
| See active convs | sidebar (default) — last 30 by updated_at | ✅ already exists |
| See archived | sidebar toggle "Show archived" | ⏳ to build |
| Pin a conv | star/pin icon in conv header | ⏳ to build |
| Delete a conv | trash icon in conv header (soft → archived; second click → purge) | ⏳ to build (delete endpoint exists) |
| Start temporary mode | "+ Temporary chat" button next to "+ New" | ⏳ to build |

UI changes deferred to a separate sprint. The backend policy ships first.

## 7. Test-pollution filter (immediate cleanup)

Existing `_SEED_TITLES_SQL = "('sec-check', 'lab-priv', 'rls-check')"` only filters 3 specific test titles. New test scripts (`kids-edu-smoke`, `freshness-accuracy-*`, etc.) accumulated.

Fix in `applications/sediment_platform/routers/conversations.py`:

```python
_TEST_TITLE_PATTERNS = [
    "sec-check", "lab-priv", "rls-check",                # legacy seed
    "kids-edu-smoke", "freshness-accuracy-",             # my smoke runs
    "citation-precision", "cross-tenant-iso",            # accuracy framework
    "discord-reply-",                                    # gateway runner (now removed)
    "probe", "probe2", "test:",                          # human probes
    "openclaw-",                                          # future OpenClaw bot
]

def _exclude_test_sql() -> str:
    """SQL fragment that filters out test-prefix titles in list_convs."""
    or_clauses = " OR ".join(
        f"title ILIKE '{p}%'" for p in _TEST_TITLE_PATTERNS
    )
    return f" AND NOT ({or_clauses})"
```

And in `list_convs`, `include_test=false` default applies this filter; `include_test=true` shows all.

## 8. One-shot historical cleanup

A migration script `scripts/cleanup_test_conversations.py` walks the existing table and deletes conversations matching the test patterns. Idempotent. Logs per-tenant counts.

## 9. Coverage matrix

| Tenant | Active convs | Archived (would archive) | Test pollution to clean |
|---|---|---|---|
| hypeproof-lab | ~20 real | ~unknown (none archived yet) | ~30+ pollution rows |
| kids-edu | 0 real (no live use) | 0 | 5+ pollution rows |
| acme-test | 0 (RLS test only) | 0 | 0 |

Numbers refresh after first sweep.

## 10. Boundary principle

> **Retention policy is enforced server-side. Clients never decide what to keep.**

- Frontend can REQUEST temporary mode at creation, REQUEST pin/unpin, REQUEST delete
- Backend enforces the actual retention by cron — clients can't pretend a conv is "really pinned" or "really not pinned" to bypass deletion
- Decisions/actions extracted from a conv survive the conv's deletion (the `decisions.conv_id` FK is SET NULL on delete, not CASCADE)

## 11. Open questions

- **Q1**: Should we offer per-tenant retention overrides? Some tenants might want longer retention (e.g., regulated industries) or shorter (privacy-paranoid). *Recommended:* hard-default 30/90 for v1; add `tenants.feature_flags.retention_days_active/archived` for v1.5 if asked.
- **Q2**: Compliance export — before purge, do we offer the user a CSV/JSON of their conv history? *Recommended:* yes, manual via API (`GET /api/v1/admin/export?since=...`). UI later.
- **Q3**: Search-indexed convs vs archived — does search scope shrink to active only? *Recommended:* yes by default (sidebar parity); admin can scope wider.

## 12. References

- `services/sediment/applications/sediment_platform/routers/conversations.py` — list_convs filter
- `services/sediment/scripts/cleanup_test_conversations.py` (NEW) — one-shot pollution scrub
- `services/sediment/scripts/scheduler.py` — `_run_retention_sweep` registration
- `services/sediment/config/cron.yaml` — `retention_sweep` schedule
- `infra/init.sql` — column additions (via runtime ALTER in seed_lab.py, since init.sql is guard.json-locked)
- [05-distillation-pipeline.md](./05-distillation-pipeline.md) — Phase 4 consolidator (the thing that extracts the durable knowledge before the conv ages out)

## Changelog
- 2026-05-22 — v0.1 — policy locked, schema specified, cleanup path defined.
