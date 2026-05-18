"""Memory consolidation — Sunday 02:00 cron.

Three jobs (per tenant):
  1. archive: messages older than 90 days flagged archived=true
  2. promote: chunks cited in ≥ 3 conversations get boost += 0.05
  3. extract: scan new conversations for decisions/actions via LLM

Iterates over ALL tenants. Uses service role.
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lab_lib.db import service_session  # noqa: E402
from lab_lib.logging import configure_logging, get_logger  # noqa: E402
from lab_lib.settings import settings  # noqa: E402

configure_logging()
log = get_logger("dream")


async def list_tenants() -> list[dict]:
    async with service_session() as s:
        r = await s.execute(text("SELECT id::text, slug, display_name FROM tenants WHERE status = 'active'"))
        return [dict(row._mapping) for row in r]


async def archive_old_messages(tenant_id: str, ttl_days: int = 90) -> int:
    async with service_session() as s:
        await s.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id})
        r = await s.execute(text("""
            UPDATE messages SET archived = true
            WHERE tenant_id = :tid AND archived = false
              AND ts < now() - (:d || ' days')::interval
        """), {"tid": tenant_id, "d": str(ttl_days)})
        return r.rowcount or 0


async def promote_cited_chunks(tenant_id: str) -> int:
    """Boost chunks that appear in ≥ 3 distinct conversations' citations."""
    async with service_session() as s:
        await s.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id})
        # Extract chunk_ids from messages.citations (jsonb arrays)
        r = await s.execute(text("""
            WITH cites AS (
              SELECT DISTINCT m.conv_id, (cit ->> 'chunk_id')::uuid AS chunk_id
              FROM messages m, jsonb_array_elements(m.citations) cit
              WHERE m.tenant_id = :tid AND cit ? 'chunk_id'
            ),
            counts AS (
              SELECT chunk_id, count(DISTINCT conv_id) AS n
              FROM cites GROUP BY chunk_id HAVING count(DISTINCT conv_id) >= 3
            )
            UPDATE chunks SET boost = LEAST(boost + 0.05, 0.5)
            WHERE id IN (SELECT chunk_id FROM counts) AND tenant_id = :tid
        """), {"tid": tenant_id})
        return r.rowcount or 0


async def extract_decisions_actions(tenant_id: str, lookback_hours: int = 168) -> dict[str, int]:
    """Delegate to scripts.consolidate_memory — the Phase 4 worker.

    Previous implementation was a keyword-trigger stub that inserted raw
    message content as decision topics with no dedup → table polluted with
    chat queries treated as decisions. The new worker uses Anthropic
    tool-use to extract structured (topic, body, owner, due_date), dedupes
    on (tenant, topic, conv_id), and is idempotent.

    Returns {decisions, actions} counts aggregated across all consolidated
    conversations.
    """
    from datetime import datetime, timedelta, timezone
    from scripts.consolidate_memory import _list_conversations, consolidate_conv

    counts = {"decisions": 0, "actions": 0}
    # Find tenant slug for the listing helper
    async with service_session() as s:
        r = await s.execute(text("SELECT slug FROM tenants WHERE id = :tid"), {"tid": tenant_id})
        slug = r.scalar_one_or_none()
    if not slug:
        return counts

    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    convs = await _list_conversations(since, None, slug)
    for c in convs:
        try:
            r = await consolidate_conv(c, dry_run=False)
            if r.get("status") == "ok":
                counts["decisions"] += r.get("decisions", 0)
                counts["actions"] += r.get("actions", 0)
        except Exception as e:
            log.warning("dream.consolidate.error", conv_id=c["id"], err=str(e)[:200])
    return counts


async def maintain_usage_daily(tenant_id: str) -> int:
    """Roll up usage_events into usage_daily (idempotent upsert)."""
    async with service_session() as s:
        await s.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id})
        r = await s.execute(text("""
            INSERT INTO usage_daily (tenant_id, date, query_count, tokens_total, cost_cents)
            SELECT tenant_id, date_trunc('day', ts)::date, count(*),
                   COALESCE(sum(tokens_in + tokens_out), 0),
                   COALESCE(sum(cost_cents), 0)
            FROM usage_events
            WHERE tenant_id = :tid AND ts >= now() - interval '2 days'
            GROUP BY tenant_id, date_trunc('day', ts)
            ON CONFLICT (tenant_id, date) DO UPDATE SET
              query_count = EXCLUDED.query_count,
              tokens_total = EXCLUDED.tokens_total,
              cost_cents = EXCLUDED.cost_cents
        """), {"tid": tenant_id})
        return r.rowcount or 0


async def run_for_tenant(t: dict) -> dict[str, Any]:
    log.info("dream.tenant.start", slug=t["slug"])
    archived = await archive_old_messages(t["id"])
    promoted = await promote_cited_chunks(t["id"])
    extracted = await extract_decisions_actions(t["id"])
    rolled = await maintain_usage_daily(t["id"])
    out = {
        "tenant": t["slug"],
        "archived": archived,
        "boost_promoted": promoted,
        "decisions_extracted": extracted["decisions"],
        "actions_extracted": extracted["actions"],
        "usage_daily_rows": rolled,
    }
    log.info("dream.tenant.done", **out)
    return out


async def main():
    tenants = await list_tenants()
    log.info("dream.start", n_tenants=len(tenants))
    results = []
    for t in tenants:
        try:
            r = await run_for_tenant(t)
            results.append(r)
        except Exception:
            log.exception("dream.tenant.error", slug=t["slug"])
    log.info("dream.done", results=results)
    print(json.dumps({"status": "ok", "results": results}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
