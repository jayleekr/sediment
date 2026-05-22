"""Cross-tenant RLS verification.

Creates 2 tenants (hypeproof-lab + acme-test), inserts artifacts in each,
then queries with each tenant context — asserts only own rows visible.

Run: make verify-rls
Exits non-zero if RLS leaks.
"""
from __future__ import annotations
import asyncio
import sys
from pathlib import Path
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lab_lib.db import app_session, service_session  # noqa: E402
from lab_lib.logging import configure_logging, get_logger  # noqa: E402

configure_logging()
log = get_logger("verify_rls")


async def get_tenant_ids() -> tuple[str, str]:
    async with service_session() as s:
        r1 = (await s.execute(text("SELECT id FROM tenants WHERE slug='hypeproof-lab'"))).first()
        r2 = (await s.execute(text("SELECT id FROM tenants WHERE slug='acme-test'"))).first()
        if not r1 or not r2:
            raise RuntimeError("Both tenants must exist — run `make seed` first.")
        return str(r1[0]), str(r2[0])


async def insert_marker(tid: str, marker: str) -> None:
    async with service_session() as s:
        await s.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tid})
        row = await s.execute(text("""
            INSERT INTO artifacts (tenant_id, ref, type, body, frontmatter)
            VALUES (:tid, :ref, 'note', :body, '{}'::jsonb)
            ON CONFLICT (tenant_id, ref) DO UPDATE SET body = EXCLUDED.body, updated_at = now()
            RETURNING id
        """), {"tid": tid, "ref": f"rls-test/{marker}.md", "body": f"marker={marker}"})
        artifact_id = str(row.scalar_one())
        await s.execute(text("""
            DELETE FROM chunks
            WHERE tenant_id = CAST(:tid AS uuid) AND artifact_id = CAST(:aid AS uuid)
        """), {"tid": tid, "aid": artifact_id})
        await s.execute(text("""
            INSERT INTO chunks (tenant_id, artifact_id, seq, content)
            VALUES (CAST(:tid AS uuid), CAST(:aid AS uuid), 0, :content)
        """), {
            "tid": tid,
            "aid": artifact_id,
            "content": f"RLS search marker {marker} tenant isolation canary",
        })


async def count_with_tenant(view_as: str, marker_a: str, marker_b: str) -> tuple[int, int]:
    """As app role, count own marker visibility."""
    async with app_session(view_as) as s:
        a = (await s.execute(text("SELECT count(*) FROM artifacts WHERE body = :m"),
                             {"m": f"marker={marker_a}"})).scalar_one()
        b = (await s.execute(text("SELECT count(*) FROM artifacts WHERE body = :m"),
                             {"m": f"marker={marker_b}"})).scalar_one()
    return a, b


async def main() -> int:
    tid_lab, tid_acme = await get_tenant_ids()
    log.info("verify_rls.tenants", lab=tid_lab, acme=tid_acme)

    await insert_marker(tid_lab, "lab-only-marker-RLS")
    await insert_marker(tid_acme, "acme-only-marker-RLS")

    # As lab, should see lab=1, acme=0
    a, b = await count_with_tenant(tid_lab, "lab-only-marker-RLS", "acme-only-marker-RLS")
    log.info("rls.lab.view", lab=a, acme=b)
    fail_lab = (a != 1) or (b != 0)

    # As acme, should see lab=0, acme=1
    c, d = await count_with_tenant(tid_acme, "lab-only-marker-RLS", "acme-only-marker-RLS")
    log.info("rls.acme.view", lab=c, acme=d)
    fail_acme = (c != 0) or (d != 1)

    if fail_lab or fail_acme:
        log.error("RLS_LEAK_DETECTED",
                  lab_view={"lab": a, "acme": b},
                  acme_view={"lab": c, "acme": d})
        return 2
    log.info("rls.ok — cross-tenant isolation verified")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
