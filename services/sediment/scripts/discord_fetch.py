"""Discord fetcher — pulls live messages from a channel into the events table.

Replaces the JSON-fixture pattern in `discord_ingest.py` with direct Discord
HTTP API access. Uses `lab_lib.connectors.discord.DiscordConnector`.

Usage:
    # One-shot backfill: last 100 messages from one channel
    python -m scripts.discord_fetch --channel-id 1468135779271180502 \\
        --channel-name daily-research --limit 100

    # Watermarked incremental: pulls only new messages since last run
    python -m scripts.discord_fetch --channel-id 1468135779271180502 \\
        --channel-name daily-research --incremental

    # List all channels the bot can see (one-time, for tenant config)
    python -m scripts.discord_fetch --list-resources

    # Dry-run: fetch + print summary, don't write to DB
    python -m scripts.discord_fetch --channel-id <id> --dry-run

Watermark storage (sediment#163):
    The watermark (last seen message_id per channel) lives in
    `integrations.config.state.watermarks.<channel_id>` — the same place and
    the same `jsonb_set` mechanism the GitHub connector already uses
    (`github_repo_fetch._save_state`). `integrations` is the canonical store
    for connector config and state; there is no separate
    `tenant_connector_state` table and none is needed.

    Before #163 the watermark was DERIVED on read, as
    `MAX(events.payload->>'message_id')` for the channel. That still works and
    is kept as the fallback, which is what makes the transition seamless: an
    existing deployment has no stored watermark but does have events, so the
    first run after this change resumes exactly where it left off and stores
    the watermark from then on.

Exit codes:
    0  ok
    1  config error (no token / DB unreachable / channel forbidden)
    2  partial (some messages failed insert; check logs)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from sqlalchemy import text

from lab_lib.connectors.discord import (
    DiscordConnector,
    NormalizedEvent,
    Resource,
)
from lab_lib.db import service_session
from lab_lib.logging import configure_logging, get_logger
from lab_lib.settings import settings

configure_logging()
log = get_logger("discord_fetch")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _default_tenant_id() -> str | None:
    async with service_session() as s:
        r = await s.execute(
            text("SELECT id::text FROM tenants WHERE slug = :sl"),
            {"sl": settings.default_tenant_slug},
        )
        row = r.first()
        return row[0] if row else None


async def load_discord_integrations(tenant_slug: str | None = None) -> list[dict]:
    """Discord connector rows, one per tenant. Mirrors github_repo_fetch.

    Returns [{id, tenant_id, slug, config}]. An empty list means no tenant has
    configured Discord capture in the DB — the caller falls back to
    config/cron.yaml so an existing single-tenant deployment keeps capturing
    (sediment#163).
    """
    sql = """
        SELECT i.id::text, i.tenant_id::text, t.slug, i.config
        FROM integrations i
        JOIN tenants t ON t.id = i.tenant_id
        WHERE i.kind = 'discord'
    """
    params: dict[str, Any] = {}
    if tenant_slug:
        sql += " AND t.slug = :slug"
        params["slug"] = tenant_slug
    async with service_session() as s:
        r = await s.execute(text(sql), params)
        return [
            {"id": row[0], "tenant_id": row[1], "slug": row[2], "config": row[3] or {}}
            for row in r.fetchall()
        ]


def channels_from_config(config: dict) -> list[dict]:
    """Opted-in channels for one tenant: `config.resources` (base.py's term).

    Accepts `{"resources": [{"id", "name"}]}`. Rows without an id are dropped —
    a channel we cannot address is not a channel.
    """
    out = []
    for res in (config or {}).get("resources") or []:
        if isinstance(res, dict) and res.get("id"):
            out.append({"id": str(res["id"]), "name": res.get("name") or str(res["id"])})
    return out


async def save_watermark(integration_id: str, channel_id: str, watermark: str) -> None:
    """Persist one channel's watermark into integrations.config.

    Single-statement `jsonb_set` rather than read-modify-write: two channels
    updating concurrently serialize on the row lock and each re-reads the
    committed value, so both survive. That is the property that makes one
    JSONB row per (tenant, source) safe here.
    """
    if not (integration_id and channel_id and watermark):
        return
    async with service_session() as s:
        await s.execute(text("""
            UPDATE integrations
            SET config = jsonb_set(
                    COALESCE(config, '{}'::jsonb),
                    ARRAY['state', 'watermarks', :cid],
                    to_jsonb(CAST(:wm AS text)),
                    true),
                last_sync_at = now()
            WHERE id = CAST(:id AS uuid)
        """), {"id": integration_id, "cid": channel_id, "wm": watermark})
        await s.commit()


def stored_watermark(config: dict, channel_id: str) -> str | None:
    """Watermark for one channel out of an integrations.config, or None."""
    try:
        return ((config or {}).get("state") or {}).get("watermarks", {}).get(channel_id)
    except AttributeError:
        return None


async def _get_watermark(tenant_id: str, channel_id: str) -> str | None:
    """Highest message_id already stored for this channel — the DERIVED
    watermark, now a fallback for when nothing is stored yet (sediment#163).

    Discord snowflakes sort lexicographically the same as numerically (both
    20 digits in current era), so MAX() works. If we ever cross to 21 digits,
    cast to bigint first.
    """
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT payload->>'message_id' AS mid
            FROM events
            WHERE tenant_id = :tid
              AND source = 'discord'
              AND payload->>'channel_id' = :cid
            ORDER BY (payload->>'message_id')::bigint DESC
            LIMIT 1
        """), {"tid": tenant_id, "cid": channel_id})
        row = r.first()
        return row[0] if row else None


async def _resolve_member_id(tenant_id: str, external_id: str | None) -> str | None:
    if not external_id:
        return None
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT id::text FROM members
            WHERE tenant_id = :tid AND external_id = :eid LIMIT 1
        """), {"tid": tenant_id, "eid": external_id})
        row = r.first()
        return row[0] if row else None


async def _insert_event(
    tenant_id: str,
    event: NormalizedEvent,
) -> bool:
    """Insert one event row. Returns True on insert, False on dedup-skip.

    Dedup key: (tenant_id, source, payload->>'message_id'). The
    events table doesn't have a unique constraint on that pair yet
    (TBD migration), so we do an explicit pre-check. Cheap because
    message_id sits in the JSONB GIN index.
    """
    async with service_session() as s:
        existing = await s.execute(text("""
            SELECT 1 FROM events
            WHERE tenant_id = :tid AND source = 'discord'
              AND payload->>'message_id' = :mid LIMIT 1
        """), {"tid": tenant_id, "mid": event.external_id})
        if existing.first():
            return False
        member_id = await _resolve_member_id(tenant_id, event.member_external_id)
        await s.execute(text("""
            INSERT INTO events (tenant_id, source, kind, member_id, payload, ts)
            VALUES (:tid, 'discord', :kind, :mid, CAST(:payload AS jsonb), :ts)
        """), {
            "tid": tenant_id,
            "kind": event.kind,
            "mid": member_id,
            "payload": json.dumps(event.payload, default=str),
            "ts": event.ts,
        })
        await s.commit()
        return True


# ---------------------------------------------------------------------------
# Main flows
# ---------------------------------------------------------------------------

async def cmd_list_resources(token: str | None) -> int:
    conn = DiscordConnector(token=token)
    try:
        resources = await conn.list_resources()
    finally:
        await conn.aclose()
    print(json.dumps([
        {
            "id": r.id, "name": r.name, "kind": r.kind,
            "guild_name": r.extra.get("guild_name"),
            "topic": r.extra.get("topic"),
        } for r in resources
    ], indent=2, ensure_ascii=False))
    return 0


async def cmd_fetch(args: argparse.Namespace) -> int:
    token = os.environ.get("HYPEPROOF_DISCORD_BOT_TOKEN") or os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        print("ERROR: HYPEPROOF_DISCORD_BOT_TOKEN (or DISCORD_BOT_TOKEN) not set",
              file=sys.stderr)
        return 1

    # Resolve tenant + watermark (sediment#163)
    tenant_id = getattr(args, "tenant_id", None)
    integration_id = getattr(args, "integration_id", None)
    integration_config = getattr(args, "integration_config", None) or {}
    after_external_id: str | None = args.after
    if not args.dry_run:
        if not tenant_id:
            # No tenant supplied by the caller — single-tenant path, as before.
            try:
                tenant_id = await _default_tenant_id()
            except Exception as e:
                print(f"ERROR: DB unreachable: {e}", file=sys.stderr)
                return 1
            if not tenant_id:
                print("ERROR: default tenant missing — run `make seed`", file=sys.stderr)
                return 1
        if args.incremental and not after_external_id:
            # Stored watermark first; fall back to the derived MAX(events...)
            # so a deployment that predates #163 resumes where it left off
            # instead of re-fetching the channel from the beginning.
            after_external_id = stored_watermark(integration_config, args.channel_id)
            source = "stored"
            if not after_external_id:
                after_external_id = await _get_watermark(tenant_id, args.channel_id)
                source = "derived"
            log.info("discord.watermark.loaded", channel=args.channel_id,
                     after=after_external_id, source=source)

    # Fetch from Discord
    conn = DiscordConnector(token=token)
    try:
        resource = Resource(
            id=args.channel_id,
            name=args.channel_name or args.channel_id,
            kind="channel",
            extra={},
        )
        events = await conn.fetch_since(
            resource, after_external_id, limit=args.limit,
        )
    finally:
        await conn.aclose()

    # Insert or summarize
    inserted = 0
    skipped = 0
    if args.dry_run:
        for e in events[:5]:
            preview = (e.payload.get("content") or "")[:80].replace("\n", " ")
            print(f"  [{e.payload.get('author')}] {preview}")
        print(json.dumps({
            "channel": resource.name,
            "fetched": len(events),
            "dry_run": True,
            "watermark_used": after_external_id,
            "last_message_id": events[-1].external_id if events else None,
        }, indent=2, ensure_ascii=False))
        return 0

    for e in events:
        try:
            if await _insert_event(tenant_id, e):
                inserted += 1
            else:
                skipped += 1
        except Exception as ex:
            log.warning("discord.insert.failed", mid=e.external_id, err=str(ex)[:160])
    # Advance the stored watermark only for messages we actually persisted.
    # Advancing on fetch instead would skip past anything that failed to
    # insert, and the gap would never be retried.
    new_watermark = events[-1].external_id if events and inserted else None
    if integration_id and new_watermark:
        try:
            await save_watermark(integration_id, args.channel_id, new_watermark)
        except Exception as ex:
            # Non-fatal: the derived fallback still finds these messages next
            # run. Losing the store costs a wider re-scan, not data.
            log.warning("discord.watermark.save_failed",
                        channel=args.channel_id, err=str(ex)[:160])
    print(json.dumps({
        "channel": resource.name,
        "fetched": len(events),
        "inserted": inserted,
        "skipped_duplicates": skipped,
        "watermark_used": after_external_id,
        "watermark_stored": new_watermark if integration_id else None,
        "last_message_id": events[-1].external_id if events else None,
    }, indent=2, ensure_ascii=False))
    return 0 if inserted == len(events) - skipped else 2


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Fetch Discord messages into events")
    ap.add_argument("--list-resources", action="store_true",
                    help="List all channels the bot can see and exit")
    ap.add_argument("--channel-id", help="Discord channel snowflake")
    ap.add_argument("--channel-name", default="",
                    help="Display name for the channel (used in payload)")
    ap.add_argument("--limit", type=int, default=100,
                    help="Max messages per fetch (Discord caps at 100)")
    ap.add_argument("--after", default=None,
                    help="Explicit watermark (message_id) — overrides --incremental")
    ap.add_argument("--incremental", action="store_true",
                    help="Use max(events.payload->>'message_id') as watermark")
    ap.add_argument("--dry-run", action="store_true",
                    help="Fetch + print, don't write to DB")
    return ap


def main() -> int:
    args = _build_parser().parse_args()
    if args.list_resources:
        return asyncio.run(cmd_list_resources(token=None))
    if not args.channel_id:
        print("ERROR: --channel-id required (or --list-resources)", file=sys.stderr)
        return 1
    return asyncio.run(cmd_fetch(args))


if __name__ == "__main__":
    sys.exit(main())
