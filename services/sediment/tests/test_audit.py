"""Tests for the mcp_call_log audit writer (`lab_lib/audit.py`)."""
from __future__ import annotations
import asyncio
import os

import pytest
from sqlalchemy import text

from lab_lib.audit import audit_log, _write
from lab_lib.db import service_session


pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_DB") == "1", reason="DB not available"
)


# Use any existing tenant + member from the seed for these tests.
async def _ids():
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT m.id::text, m.tenant_id::text FROM members m LIMIT 1
        """))
        row = r.first()
    if not row:
        pytest.skip("no members seeded")
    return row


async def test_audit_log_writes_row():
    member_id, tenant_id = await _ids()
    await _write(tenant_id, member_id, "test_tool", "cli",
                 42, 1024, 200, None)
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT tool, client, latency_ms, result_bytes, status_code
            FROM mcp_call_log
            WHERE member_id = CAST(:m AS uuid) AND tool = 'test_tool'
            ORDER BY id DESC LIMIT 1
        """), {"m": member_id})
        row = r.first()
    assert row is not None
    assert row[0] == "test_tool"
    assert row[1] == "cli"
    assert row[2] == 42
    assert row[3] == 1024


async def test_audit_log_records_error_reason():
    member_id, tenant_id = await _ids()
    await _write(tenant_id, member_id, "search", "mcp-shim",
                 88, 0, 429, "rate_limited")
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT status_code, error_reason FROM mcp_call_log
            WHERE member_id = CAST(:m AS uuid) AND tool = 'search'
              AND status_code = 429
            ORDER BY id DESC LIMIT 1
        """), {"m": member_id})
        row = r.first()
    assert row is not None
    assert row[0] == 429
    assert row[1] == "rate_limited"


async def test_audit_log_fire_and_forget_returns_immediately():
    """audit_log() must return synchronously even when DB is slow."""
    member_id, tenant_id = await _ids()
    import time
    start = time.monotonic()
    audit_log(tenant_id, member_id, "fast_path", latency_ms=1,
              client="cli", status_code=200)
    elapsed = time.monotonic() - start
    assert elapsed < 0.05, f"audit_log blocked for {elapsed:.3f}s"
    # Let the task land
    await asyncio.sleep(0.3)


async def test_audit_log_failure_does_not_raise():
    """Hand it nonsense — it must swallow the failure."""
    # Bogus UUIDs → FK violation in mcp_call_log
    await _write(
        "00000000-0000-0000-0000-000000000000",
        "00000000-0000-0000-0000-000000000000",
        "test_fail", "cli", 0, 0, 200, None,
    )
    # If we got here, _write swallowed the error — pass.


async def test_audit_log_with_long_error_reason_doesnt_truncate_in_v1():
    """v1 does not truncate; the column is TEXT. This test documents the
    current behavior so a future caller adding truncation has a regression
    case to update."""
    member_id, tenant_id = await _ids()
    long_reason = "x" * 5000
    await _write(tenant_id, member_id, "long_err", "cli",
                 1, 0, 500, long_reason)
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT length(error_reason) FROM mcp_call_log
            WHERE member_id = CAST(:m AS uuid) AND tool = 'long_err'
            ORDER BY id DESC LIMIT 1
        """), {"m": member_id})
        row = r.first()
    assert row is not None
    assert row[0] == 5000
