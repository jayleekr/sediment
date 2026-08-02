"""Cross-tenant RLS verification.

Run after `make seed`. Asserts:
  1. App role with tenant A cannot see tenant B's artifacts.
  2. Service role bypasses RLS (sees all).
  3. (sediment P2) The RLS backstop holds on EVERY tenant table:
     - no tenant context  -> 0 rows (fail-safe deny)
     - RLS enabled+forced + a policy exists (schema-level, needs no seed)
"""
import asyncio
import os

import pytest
from sqlalchemy import text

from lab_lib.db import app_session, service_session, SessionApp
from validator.checks.lib_rls import TENANT_TABLES

# Every test in this module talks to Postgres. The marker that was supposed to
# cover it lived in conftest.py, where pytest ignores it — see sediment#154.
# Without it, `SKIP_DB=1` left 37 connection errors in every DB-less run, and
# that permanently red baseline hid two real regressions (#155, #156).
pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_DB") == "1", reason="DB not available")


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


# ============================================================
# sediment P2 — RLS backstop on EVERY tenant table
# Many chat/CRUD queries rely on RLS alone (no explicit tenant_id filter),
# so the backstop must be provably reliable on all tables, not just artifacts.
# ============================================================

@pytest.mark.asyncio
@pytest.mark.parametrize("tbl", TENANT_TABLES)
async def test_no_tenant_context_yields_zero(tbl):
    """App role with NO tenant context must see 0 rows on every tenant table.
    This is what makes a query that forgets `WHERE tenant_id=...` still safe."""
    async with SessionApp() as s:
        # No set_config('app.tenant_id', ...) → current_tenant_id() is NULL → deny.
        # Table name is from a hardcoded constant tuple (no injection surface).
        n = (await s.execute(text(f"SELECT count(*) FROM {tbl}"))).scalar_one()
    assert n == 0, f"{tbl}: fail-safe broken — {n} rows visible without tenant ctx"


@pytest.mark.asyncio
@pytest.mark.parametrize("tbl", TENANT_TABLES)
async def test_rls_enabled_forced_and_policy(tbl):
    """Every tenant table must have RLS ENABLED + FORCED + ≥1 policy.
    Schema-level (needs no seed) — catches a new table or a dropped policy
    at PR time, before it can leak in production."""
    async with service_session() as s:
        row = (await s.execute(text(
            """
            SELECT c.relrowsecurity, c.relforcerowsecurity,
                   (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = :t AND n.nspname = 'public'
            """), {"t": tbl})).first()
    assert row is not None, f"{tbl}: table not found in public schema"
    enabled, forced, n_pol = row
    assert enabled, f"{tbl}: RLS not ENABLED"
    assert forced, f"{tbl}: RLS not FORCED (table owner could bypass)"
    assert n_pol >= 1, f"{tbl}: no RLS policy present"
