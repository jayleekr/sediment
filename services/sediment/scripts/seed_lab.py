"""Seed default tenant + members from ../../data/members.json.

Idempotent — safe to run multiple times.
Run: make seed
"""
from __future__ import annotations
import asyncio
import json
import sys
from pathlib import Path
from sqlalchemy import text

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
        await s.execute(text(
            "ALTER TABLE members ADD COLUMN IF NOT EXISTS github_login TEXT"
        ))
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

    log.info("seed.done")


if __name__ == "__main__":
    asyncio.run(main())
