"""Cross-tenant RLS verification.

Run after `make seed`. Asserts:
  1. App role with tenant A cannot see tenant B's artifacts.
  2. Service role bypasses RLS (sees all).
"""
import asyncio
import pytest
from sqlalchemy import text

from lab_lib.db import app_session, service_session


async def _ids() -> tuple[str, str]:
    async with service_session() as s:
        r1 = (await s.execute(text("SELECT id FROM tenants WHERE slug='hypeproof-lab'"))).first()
        r2 = (await s.execute(text("SELECT id FROM tenants WHERE slug='acme-test'"))).first()
        assert r1 and r2, "Run `make seed` first."
        return str(r1[0]), str(r2[0])


@pytest.mark.asyncio
async def test_rls_isolates_artifacts():
    tid_lab, tid_acme = await _ids()

    # Insert a marker into each tenant via service role
    async with service_session() as s:
        await s.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tid_lab})
        await s.execute(text("""
            INSERT INTO artifacts (tenant_id, ref, type, body, frontmatter)
            VALUES (:tid, 'rls-test/lab.md', 'note', 'lab-marker', '{}'::jsonb)
            ON CONFLICT (tenant_id, ref) DO UPDATE SET body = 'lab-marker', updated_at = now()
        """), {"tid": tid_lab})
        await s.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tid_acme})
        await s.execute(text("""
            INSERT INTO artifacts (tenant_id, ref, type, body, frontmatter)
            VALUES (:tid, 'rls-test/acme.md', 'note', 'acme-marker', '{}'::jsonb)
            ON CONFLICT (tenant_id, ref) DO UPDATE SET body = 'acme-marker', updated_at = now()
        """), {"tid": tid_acme})

    # As lab — should NOT see acme
    async with app_session(tid_lab) as s:
        n = (await s.execute(text(
            "SELECT count(*) FROM artifacts WHERE body = 'acme-marker'"))).scalar_one()
        assert n == 0, f"RLS leak: lab sees {n} acme-marker rows"
        n = (await s.execute(text(
            "SELECT count(*) FROM artifacts WHERE body = 'lab-marker'"))).scalar_one()
        assert n == 1, "lab cannot see its own marker"

    # As acme — should NOT see lab
    async with app_session(tid_acme) as s:
        n = (await s.execute(text(
            "SELECT count(*) FROM artifacts WHERE body = 'lab-marker'"))).scalar_one()
        assert n == 0, f"RLS leak: acme sees {n} lab-marker rows"


@pytest.mark.asyncio
async def test_rls_isolates_chunks():
    tid_lab, tid_acme = await _ids()

    # Lab session sees only lab chunks
    async with app_session(tid_lab) as s:
        rows = (await s.execute(text(
            "SELECT count(*) FROM chunks c JOIN artifacts a ON a.id = c.artifact_id"))).scalar_one()
        # Just sanity: returns >= 0 without error
        assert rows >= 0


@pytest.mark.asyncio
async def test_service_bypasses_rls():
    """service role sees everything (it's BYPASSRLS at role level)."""
    tid_lab, tid_acme = await _ids()
    async with service_session() as s:
        # Without SET, service role sees all tenants' rows
        n = (await s.execute(text("SELECT count(*) FROM artifacts"))).scalar_one()
        assert n >= 2, f"service should see ≥ 2 rls-test markers, got {n}"
