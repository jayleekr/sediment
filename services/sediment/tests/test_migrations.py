"""Tests for lab_lib.migrations.add_column_safely.

Per sediment#46 — the helper extracts the try-session/owner-fallback/
tolerate-already-exists dance from two copy-pasted spots. These tests
verify each of those three branches without touching a live DB.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lab_lib.migrations import add_column_safely, add_columns_safely


def _mock_session(*, raise_on_execute: Exception | None = None) -> MagicMock:
    """Build a mock AsyncSession with the methods add_column_safely calls."""
    s = MagicMock()
    s.execute = AsyncMock(side_effect=raise_on_execute)
    s.commit = AsyncMock()
    s.rollback = AsyncMock()
    return s


# ---------------------------------------------------------------------------
# Path 1: session ALTER succeeds (dev/local-owner case)
# ---------------------------------------------------------------------------

async def test_session_path_returns_session_and_commits():
    s = _mock_session()
    result = await add_column_safely(
        s, table="x", column="c",
        ddl="ALTER TABLE x ADD COLUMN IF NOT EXISTS c TEXT",
        owner_url_fn=lambda: "should_not_be_called",
    )
    assert result == "session"
    s.execute.assert_awaited_once()
    s.commit.assert_awaited_once()
    s.rollback.assert_not_awaited()


# ---------------------------------------------------------------------------
# Path 2: session fails (no privilege) → owner connection succeeds
# ---------------------------------------------------------------------------

async def test_owner_fallback_path():
    from sqlalchemy.exc import ProgrammingError
    # Make session.execute raise a real SQLAlchemyError
    exc = ProgrammingError("ALTER TABLE", {}, Exception("must be owner"))
    s = _mock_session(raise_on_execute=exc)

    owner_conn = AsyncMock()
    owner_conn.execute = AsyncMock()
    owner_conn.close = AsyncMock()

    with patch("lab_lib.migrations.asyncpg.connect",
               new=AsyncMock(return_value=owner_conn)):
        result = await add_column_safely(
            s, table="conversations", column="pinned",
            ddl="ALTER TABLE conversations ADD COLUMN IF NOT EXISTS pinned BOOLEAN",
            owner_url_fn=lambda: "postgresql://owner@host/db",
        )

    assert result == "owner"
    s.execute.assert_awaited_once()
    s.rollback.assert_awaited_once()
    owner_conn.execute.assert_awaited_once()
    owner_conn.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# Path 3a: session fails + owner connection itself fails → skipped
# ---------------------------------------------------------------------------

async def test_owner_unavailable_returns_skipped():
    from sqlalchemy.exc import ProgrammingError
    s = _mock_session(raise_on_execute=ProgrammingError("x", {}, Exception("nope")))

    with patch("lab_lib.migrations.asyncpg.connect",
               new=AsyncMock(side_effect=OSError("can't connect"))):
        result = await add_column_safely(
            s, table="t", column="c", ddl="ALTER TABLE t ADD COLUMN c TEXT",
            owner_url_fn=lambda: "postgresql://nope",
        )

    assert result == "skipped"


# ---------------------------------------------------------------------------
# Path 3b: session fails, owner succeeds for some DDLs but raises on a later
# one (idempotent "already exists") → tolerated, still returns "skipped"
# ---------------------------------------------------------------------------

async def test_owner_ddl_failure_tolerated():
    from sqlalchemy.exc import ProgrammingError
    s = _mock_session(raise_on_execute=ProgrammingError("x", {}, Exception("must be owner")))

    owner_conn = AsyncMock()
    owner_conn.execute = AsyncMock(side_effect=Exception("relation already exists"))
    owner_conn.close = AsyncMock()

    with patch("lab_lib.migrations.asyncpg.connect",
               new=AsyncMock(return_value=owner_conn)):
        result = await add_column_safely(
            s, table="t", column="c", ddl="CREATE INDEX IF NOT EXISTS",
            owner_url_fn=lambda: "postgresql://x",
        )

    assert result == "skipped"
    owner_conn.close.assert_awaited_once()  # always close


# ---------------------------------------------------------------------------
# add_columns_safely (bulk) preserves order + per-DDL routing
# ---------------------------------------------------------------------------

async def test_bulk_add_columns_returns_per_ddl_results():
    from sqlalchemy.exc import ProgrammingError

    call_count = {"n": 0}

    async def execute_side_effect(*_args, **_kw):
        # First DDL succeeds via session, second fails → owner fallback
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise ProgrammingError("x", {}, Exception("must be owner"))
        return MagicMock()

    s = MagicMock()
    s.execute = AsyncMock(side_effect=execute_side_effect)
    s.commit = AsyncMock()
    s.rollback = AsyncMock()

    owner_conn = AsyncMock()
    owner_conn.execute = AsyncMock()
    owner_conn.close = AsyncMock()

    with patch("lab_lib.migrations.asyncpg.connect",
               new=AsyncMock(return_value=owner_conn)):
        results = await add_columns_safely(
            s, table="conversations", owner_url_fn=lambda: "postgresql://x",
            columns=[
                {"column": "a", "ddl": "ALTER TABLE conversations ADD COLUMN a TEXT"},
                {"column": "b", "ddl": "ALTER TABLE conversations ADD COLUMN b TEXT"},
            ],
        )

    assert results == ["session", "owner"]
