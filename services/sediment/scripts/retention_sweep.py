"""Conversation retention sweep — implements design doc 15.

Daily cron job. Two steps:
  1. ARCHIVE inactive conversations (>30 days since updated_at, NOT pinned,
     not already archived). Marks `archived_at = now()`. Hidden from
     default sidebar.
  2. PURGE conversations whose `purge_after` has elapsed. Hard-delete
     conv row + cascading messages. Decisions/actions referencing the
     conv keep existing (FK SET NULL on delete).

Plus a temporary-conv path:
  - temporary=true convs get archived 1h after last message
  - and purged 24h after creation

Idempotent. Logs per-tenant counts. Posts a summary line to fly logs;
optionally a Discord summary (not wired by default — would be noise).

USAGE:
  python -m scripts.retention_sweep              # run all steps
  python -m scripts.retention_sweep --dry-run    # report-only
  python -m scripts.retention_sweep --tenant kids-edu  # one tenant
"""
from __future__ import annotations
import argparse
import asyncio
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve()
sys.path.insert(0, str(SCRIPT.parents[1]))

from sqlalchemy import text  # noqa: E402

from lab_lib.db import service_session  # noqa: E402
from lab_lib.logging import configure_logging, get_logger  # noqa: E402

configure_logging()
log = get_logger("retention_sweep")


# Retention windows (per design doc 15)
ACTIVE_DAYS = 30
PURGE_DAYS = 90
TEMP_ARCHIVE_HOURS = 1
TEMP_PURGE_HOURS = 24


async def _archive_inactive(
    tenant_slug: str | None, dry_run: bool,
) -> dict:
    """Set archived_at for convs older than ACTIVE_DAYS, not pinned."""
    where_tenant = ""
    params = {"days": ACTIVE_DAYS}
    if tenant_slug:
        where_tenant = "AND c.tenant_id = (SELECT id FROM tenants WHERE slug = :slug)"
        params["slug"] = tenant_slug

    if dry_run:
        sql = f"""
            SELECT t.slug, count(*) AS n
            FROM conversations c
            JOIN tenants t ON t.id = c.tenant_id
            WHERE c.archived_at IS NULL
              AND c.pinned = FALSE
              AND c.updated_at < now() - (:days || ' days')::interval
              {where_tenant}
            GROUP BY t.slug
        """
        async with service_session() as s:
            r = await s.execute(text(sql), params)
            return {row[0]: row[1] for row in r}
    sql = f"""
        UPDATE conversations c
        SET archived_at = now()
        FROM tenants t
        WHERE t.id = c.tenant_id
          AND c.archived_at IS NULL
          AND c.pinned = FALSE
          AND c.updated_at < now() - (:days || ' days')::interval
          {where_tenant}
        RETURNING t.slug
    """
    async with service_session() as s:
        r = await s.execute(text(sql), params)
        slugs = [row[0] for row in r]
        await s.commit()
    out: dict[str, int] = {}
    for slug in slugs:
        out[slug] = out.get(slug, 0) + 1
    return out


async def _archive_completed_temporary(
    tenant_slug: str | None, dry_run: bool,
) -> dict:
    """Temporary convs: archive 1 hour after last activity."""
    where_tenant = ""
    params = {"hrs": TEMP_ARCHIVE_HOURS}
    if tenant_slug:
        where_tenant = "AND c.tenant_id = (SELECT id FROM tenants WHERE slug = :slug)"
        params["slug"] = tenant_slug

    if dry_run:
        sql = f"""
            SELECT t.slug, count(*)
            FROM conversations c
            JOIN tenants t ON t.id = c.tenant_id
            WHERE c.temporary = TRUE
              AND c.archived_at IS NULL
              AND c.updated_at < now() - (:hrs || ' hours')::interval
              {where_tenant}
            GROUP BY t.slug
        """
        async with service_session() as s:
            r = await s.execute(text(sql), params)
            return {row[0]: row[1] for row in r}
    sql = f"""
        UPDATE conversations c
        SET archived_at = now(),
            purge_after = c.updated_at + (:purge_hrs || ' hours')::interval
        FROM tenants t
        WHERE t.id = c.tenant_id
          AND c.temporary = TRUE
          AND c.archived_at IS NULL
          AND c.updated_at < now() - (:hrs || ' hours')::interval
          {where_tenant}
        RETURNING t.slug
    """
    params["purge_hrs"] = TEMP_PURGE_HOURS
    async with service_session() as s:
        r = await s.execute(text(sql), params)
        slugs = [row[0] for row in r]
        await s.commit()
    out: dict[str, int] = {}
    for slug in slugs:
        out[slug] = out.get(slug, 0) + 1
    return out


async def _set_purge_after(tenant_slug: str | None, dry_run: bool) -> int:
    """Compute purge_after for archived convs that don't have it set."""
    if dry_run:
        async with service_session() as s:
            r = await s.execute(text("""
                SELECT count(*) FROM conversations
                WHERE archived_at IS NOT NULL
                  AND purge_after IS NULL
                  AND pinned = FALSE
                  AND temporary = FALSE
            """))
            return r.scalar() or 0
    async with service_session() as s:
        r = await s.execute(text("""
            UPDATE conversations
            SET purge_after = archived_at + (:purge_days || ' days')::interval
            WHERE archived_at IS NOT NULL
              AND purge_after IS NULL
              AND pinned = FALSE
              AND temporary = FALSE
        """), {"purge_days": PURGE_DAYS - ACTIVE_DAYS})  # 60 more days after archive
        await s.commit()
        return r.rowcount


async def _purge_expired(tenant_slug: str | None, dry_run: bool) -> dict:
    """Hard-delete conversations whose purge_after is in the past."""
    where_tenant = ""
    params: dict = {}
    if tenant_slug:
        where_tenant = "AND c.tenant_id = (SELECT id FROM tenants WHERE slug = :slug)"
        params["slug"] = tenant_slug

    if dry_run:
        sql = f"""
            SELECT t.slug, count(*)
            FROM conversations c
            JOIN tenants t ON t.id = c.tenant_id
            WHERE c.purge_after IS NOT NULL
              AND c.purge_after < now()
              AND c.pinned = FALSE
              {where_tenant}
            GROUP BY t.slug
        """
        async with service_session() as s:
            r = await s.execute(text(sql), params)
            return {row[0]: row[1] for row in r}
    sql = f"""
        WITH gone AS (
            SELECT c.id, t.slug
            FROM conversations c
            JOIN tenants t ON t.id = c.tenant_id
            WHERE c.purge_after IS NOT NULL
              AND c.purge_after < now()
              AND c.pinned = FALSE
              {where_tenant}
        ),
        deleted AS (
            DELETE FROM conversations
            WHERE id IN (SELECT id FROM gone)
            RETURNING id
        )
        SELECT gone.slug FROM gone JOIN deleted ON deleted.id = gone.id
    """
    async with service_session() as s:
        r = await s.execute(text(sql), params)
        slugs = [row[0] for row in r]
        await s.commit()
    out: dict[str, int] = {}
    for slug in slugs:
        out[slug] = out.get(slug, 0) + 1
    return out


async def amain(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tenant", help="tenant slug (omit for all)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    log.info("retention.start", tenant=args.tenant or "all", dry_run=args.dry_run)
    summary: dict = {"dry_run": args.dry_run}

    summary["archived_inactive"] = await _archive_inactive(args.tenant, args.dry_run)
    summary["archived_temporary"] = await _archive_completed_temporary(args.tenant, args.dry_run)
    summary["set_purge_after"] = await _set_purge_after(args.tenant, args.dry_run)
    summary["purged"] = await _purge_expired(args.tenant, args.dry_run)

    log.info("retention.done", **{k: v for k, v in summary.items() if k != "dry_run"})
    print(json.dumps(summary, indent=2, default=str))
    return 0


def main() -> None:
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
