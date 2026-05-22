"""Seed default tenant + members from services/sediment/data/members.json.

(Lives inside the service so it ships in the Docker image — root-level
data/ is NOT in the build context. Resolved via upward search, so dev and
container layouts both work. Runs automatically via fly.toml release_command
on every deploy; also `make seed` locally.)

Idempotent — safe to run multiple times.
Run: make seed
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
from pathlib import Path
import asyncpg
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

# Resolve data/members.json by walking up from this script. The old hardcoded
# parents[5] assumed the pre-split monorepo layout
# (mvp/products/sediment/services/curator/scripts/); after the 2026-05-18
# repo split the layout is <repo>/services/sediment/scripts/, so search
# instead of hardcoding — survives further moves.
SCRIPT = Path(__file__).resolve()


def _find_members_json() -> Path:
    for parent in SCRIPT.parents:
        cand = parent / "data" / "members.json"
        if cand.is_file():
            return cand
    raise FileNotFoundError(f"data/members.json not found above {SCRIPT}")


MEMBERS_JSON = _find_members_json()

sys.path.insert(0, str(SCRIPT.parents[1]))  # services/sediment/

from lab_lib.db import service_session  # noqa: E402
from lab_lib.logging import configure_logging, get_logger  # noqa: E402
from lab_lib.settings import settings  # noqa: E402

configure_logging()
log = get_logger("seed")


async def has_member_column(s, column_name: str) -> bool:
    r = await s.execute(text("""
        SELECT EXISTS (
          SELECT 1
          FROM information_schema.columns
          WHERE table_schema = current_schema()
            AND table_name = 'members'
            AND column_name = :column_name
        )
    """), {"column_name": column_name})
    return bool(r.scalar_one())


def migrations_db_url() -> str:
    raw = os.environ.get("SEDIMENT_MIGRATIONS_DB_URL")
    if raw:
        return raw.replace("+asyncpg", "")
    svc = settings.database_url_service.replace("+asyncpg", "")
    # Local-dev fallback: swap the dockerized service role for the owner role
    # (legacy `curator*` names — kept as-is for docker-compose compatibility).
    # PROD must set SEDIMENT_MIGRATIONS_DB_URL — the role rename to sediment_*
    # broke implicit substitution and there's no in-VM way to derive the
    # owner credentials from a Supabase pooler URL.
    return svc.replace(
        "curator_service:curator_service_local",
        "curator:curator_local_dev",
    )


async def add_github_login_column_as_owner() -> None:
    conn = await asyncpg.connect(migrations_db_url())
    try:
        await conn.execute("ALTER TABLE members ADD COLUMN IF NOT EXISTS github_login TEXT")
    finally:
        await conn.close()


async def ensure_retention_columns(s) -> None:
    """Per design doc 15. Idempotent — safe to re-run on every deploy.

    Adds: pinned, archived_at, temporary, purge_after to conversations.

    Implementation note: we DO NOT try the service-role session first
    (unlike ensure_github_login_column). `conversations` is owned by the
    superuser in prod, and the service role lacks ALTER privilege; the
    failing-then-rollback path leaves the session in an inconsistent
    state and aborts the whole seed run. Going straight to the owner
    connection is simpler and always works.

    If the owner connection itself fails (e.g. SEDIMENT_MIGRATIONS_DB_URL
    not configured for a fresh deploy), we log + continue — schema is
    almost certainly already in place from a prior deploy/manual run,
    and we'd rather boot than block release.
    """
    ddls = [
        "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ",
        "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS temporary BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS purge_after TIMESTAMPTZ",
        "CREATE INDEX IF NOT EXISTS conversations_archived_at_idx ON conversations (archived_at)",
        "CREATE INDEX IF NOT EXISTS conversations_purge_after_idx ON conversations (purge_after)",
    ]
    try:
        owner_conn = await asyncpg.connect(migrations_db_url())
    except Exception as e:
        log.warning("seed.schema.retention.owner_conn_unavailable",
                    err=str(e)[:200],
                    hint="schema likely already applied; if not, run ALTERs manually")
        return
    try:
        for ddl in ddls:
            try:
                await owner_conn.execute(ddl)
            except Exception as e:
                # Per-DDL tolerance — column might already exist if a prior
                # manual migration ran; index errors are also benign
                log.info("seed.schema.retention.ddl_warning",
                         ddl=ddl[:80], err=str(e)[:120])
    finally:
        await owner_conn.close()


async def ensure_github_login_column(s) -> None:
    """Keep old single-owner deploys migratable without breaking service roles."""
    try:
        await s.execute(text(
            "ALTER TABLE members ADD COLUMN IF NOT EXISTS github_login TEXT"
        ))
    except SQLAlchemyError as exc:
        await s.rollback()
        if await has_member_column(s, "github_login"):
            log.info("seed.schema.github_login_present", ddl_skipped=True)
            return
        try:
            await add_github_login_column_as_owner()
            log.info("seed.schema.github_login_added", via="owner_connection")
            return
        except Exception as owner_exc:
            if await has_member_column(s, "github_login"):
                log.info("seed.schema.github_login_present", ddl_skipped=True)
                return
            exc.add_note(f"owner DDL fallback failed: {owner_exc!r}")
        raise RuntimeError(
            "members.github_login is missing and the current DB role cannot add it; "
            "set SEDIMENT_MIGRATIONS_DB_URL or apply infra/init.sql/migrations "
            "with owner credentials before seed_lab"
        ) from exc


async def upsert_tenant(s, slug: str, name: str) -> str:
    r = await s.execute(text("""
        INSERT INTO tenants (slug, display_name, domain, plan, status)
        VALUES (:slug, :name, :domain, 'free', 'active')
        ON CONFLICT (slug) DO UPDATE SET display_name = EXCLUDED.display_name
        RETURNING id
    """), {"slug": slug, "name": name, "domain": f"{slug}.curator.hypeproof-ai.xyz"})
    tid = str(r.scalar_one())

    await s.execute(text("""
        INSERT INTO subscriptions (tenant_id, seat_count, query_quota_per_month, storage_quota_gb)
        VALUES (:tid, 8, 10000, 5)
        ON CONFLICT (tenant_id) DO UPDATE SET
          seat_count = EXCLUDED.seat_count,
          query_quota_per_month = EXCLUDED.query_quota_per_month,
          storage_quota_gb = EXCLUDED.storage_quota_gb
    """), {"tid": tid})
    return tid


async def upsert_member(s, tenant_id: str, m: dict) -> str:
    r = await s.execute(text("""
        INSERT INTO members (tenant_id, external_id, github_login, email, display_name, real_name, role, title, expertise, interests)
        VALUES (:tid, :eid, :gh, :email, :dn, :rn, :role, :title, CAST(:exp AS jsonb), CAST(:int AS jsonb))
        ON CONFLICT (tenant_id, email) DO UPDATE SET
          external_id = COALESCE(NULLIF(EXCLUDED.external_id, ''), members.external_id),
          github_login = COALESCE(NULLIF(EXCLUDED.github_login, ''), members.github_login),
          display_name = EXCLUDED.display_name,
          real_name = EXCLUDED.real_name,
          role = EXCLUDED.role,
          title = EXCLUDED.title,
          expertise = EXCLUDED.expertise,
          interests = EXCLUDED.interests
        RETURNING id
    """), {
        "tid": tenant_id,
        "eid": (m.get("id") or None) or None,  # empty string → NULL (NULL is exempt from UNIQUE)
        "gh": (m.get("githubLogin") or None),   # GitHub OAuth identity (SSO); NULL if unmapped
        "email": m["email"],
        "dn": m["displayName"],
        "rn": m.get("realName"),
        "role": "admin" if m.get("role") == "admin" else "creator",
        "title": m.get("title"),
        "exp": json.dumps(m.get("expertise", [])),
        "int": json.dumps(m.get("interests", [])),
    })
    return str(r.scalar_one())


async def main():
    log.info("seed.start", members_json=str(MEMBERS_JSON))
    members = json.loads(MEMBERS_JSON.read_text())["members"]

    async with service_session() as s:
        # Idempotent migration for DBs created before github_login existed
        # (fresh installs get it + the UNIQUE from init.sql).
        await ensure_github_login_column(s)
        # Idempotent migration for conversation retention (design 15).
        await ensure_retention_columns(s)
        tid = await upsert_tenant(s, settings.default_tenant_slug, settings.default_tenant_name)
        log.info("seed.tenant", id=tid, slug=settings.default_tenant_slug)
        for m in members:
            mid = await upsert_member(s, tid, m)
            log.info("seed.member", id=mid, name=m["displayName"])

        # Create a 2nd test tenant for cross-tenant verification
        tid2 = await upsert_tenant(s, "acme-test", "Acme Test (RLS verify)")
        log.info("seed.tenant", id=tid2, slug="acme-test")
        await s.execute(text("""
            INSERT INTO members (tenant_id, email, display_name, real_name, role)
            VALUES (:tid, 'admin@acme.test', 'Acme Admin', 'Acme Admin', 'admin')
            ON CONFLICT (tenant_id, email) DO NOTHING
        """), {"tid": tid2})

        # 3rd tenant: kids-edu (HypeProof Kids Edu vault — 2nd real tenant,
        # consent from Jinyong 2026-05-21). See jayleekr/sediment#13.
        tid_kids = await upsert_tenant(s, "kids-edu", "HypeProof Kids Edu")
        log.info("seed.tenant", id=tid_kids, slug="kids-edu")

        kids_admins = [
            {"email": "jayleekr0125@gmail.com", "displayName": "Jay Lee",
             "realName": "Jay Lee", "role": "admin", "githubLogin": "jayleekr"},
            {"email": "jinyong.shin@hypeproof.io", "displayName": "Jinyong Shin",
             "realName": "Jinyong Shin", "role": "admin", "githubLogin": "JinyongShin"},
        ]
        for m in kids_admins:
            await upsert_member(s, tid_kids, m)

        # GitHub integration row — config matches connector kwargs.
        # integrations has no UNIQUE constraint on (tenant_id, kind), so
        # we update existing instead of inserting to avoid duplicate rows
        # on re-seed (state.head_sha preserved across runs).
        kids_cfg = {
            "repos": ["JinyongShin/hypeproof_kids_edu"],
            "path_prefixes": ["kids_edu_vault/wiki/", "meeting_notes/"],
            "path_excludes": [".raw/", ".obsidian/", "node_modules/"],
            "extensions": [".md"],
            "branch": None,
            "schedule": "hourly_daytime_kst",  # 09-22 KST
        }
        r = await s.execute(text("""
            SELECT id, config FROM integrations
            WHERE tenant_id = CAST(:tid AS uuid) AND kind = 'github'
            LIMIT 1
        """), {"tid": tid_kids})
        existing = r.first()
        if existing is None:
            kids_cfg["state"] = {"head_sha": None}
            await s.execute(text("""
                INSERT INTO integrations (tenant_id, kind, config)
                VALUES (CAST(:tid AS uuid), 'github', CAST(:cfg AS jsonb))
            """), {"tid": tid_kids, "cfg": json.dumps(kids_cfg)})
        else:
            # Preserve runtime state (head_sha watermark) across re-seeds.
            old_cfg = existing[1] or {}
            kids_cfg["state"] = old_cfg.get("state") or {"head_sha": None}
            await s.execute(text("""
                UPDATE integrations SET config = CAST(:cfg AS jsonb)
                WHERE id = CAST(:id AS uuid)
            """), {"id": str(existing[0]), "cfg": json.dumps(kids_cfg)})

    log.info("seed.done")


if __name__ == "__main__":
    asyncio.run(main())
