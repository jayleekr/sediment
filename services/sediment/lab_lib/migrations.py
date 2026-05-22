"""Idempotent online migration helper.

Both `ensure_github_login_column` (added 2026-03) and `ensure_retention_columns`
(added 2026-05-22) hand-rolled the same pattern:

    try:
        await s.execute(text(DDL))
        await s.commit()
    except SQLAlchemyError:
        await s.rollback()
        # fallback to owner connection
        try:
            conn = await asyncpg.connect(owner_url())
            try:
                await conn.execute(DDL)
            finally:
                await conn.close()
        except Exception:
            # tolerate — column probably already exists
            ...

About 25 LOC per migration, copy-pasted, and the seed_lab.py fix for
sediment#46 had to debug the rollback semantics in two places at once.

This module collapses that to one call. Per design 15 §schema + issue #46.
"""
from __future__ import annotations

import asyncpg
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .logging import get_logger

log = get_logger("migrations")


async def add_column_safely(
    session,
    *,
    table: str,
    column: str,
    ddl: str,
    owner_url_fn,
) -> str:
    """Apply an `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` (or similar
    idempotent DDL) using the session role first, falling back to the
    owner connection if the session lacks privileges, and tolerating
    already-exists errors at the end.

    Returns one of: "session", "owner", "skipped" — useful for tests +
    structured logging.

    Args:
        session: an open AsyncSession to try first
        table:   table being altered (for logs only)
        column:  column being added/changed (for logs only)
        ddl:     full SQL statement (idempotent — use `IF NOT EXISTS` etc.)
        owner_url_fn: callable returning a postgres URL with owner/superuser
                      privilege (e.g. seed_lab.migrations_db_url). Called
                      only when the session path fails.

    The CALLER owns commit/rollback semantics for the session it passes in.
    On the happy path we commit() the session ourselves so the DDL lands
    before the function returns; on the rollback path the caller's session
    is rolled back to a clean state and the owner conn carries the change.

    Why not have one path? In dev (`make seed` against the local docker
    cluster), the session role IS the owner — no need for a second
    connection. In prod (Supabase) the service role lacks ALTER privilege
    on most tables. Trying session-first gives both environments fast
    paths.
    """
    try:
        await session.execute(text(ddl))
        await session.commit()
        log.info("migration.session_path", table=table, column=column)
        return "session"
    except SQLAlchemyError as exc:
        await session.rollback()
        log.info("migration.fallback_owner",
                 table=table, column=column, err=str(exc)[:120])

    try:
        owner_conn = await asyncpg.connect(owner_url_fn())
    except Exception as e:
        log.warning("migration.owner_conn_unavailable",
                    table=table, column=column, err=str(e)[:200],
                    hint="DDL not applied this run — verify column exists out of band")
        return "skipped"

    try:
        await owner_conn.execute(ddl)
        log.info("migration.owner_path", table=table, column=column)
        return "owner"
    except Exception as e:
        # Already exists / permission edge case / index already there —
        # tolerate, do not raise. This matches the prior hand-rolled
        # `ensure_*` pattern (warn + continue).
        log.info("migration.ddl_tolerated",
                 table=table, column=column, err=str(e)[:120])
        return "skipped"
    finally:
        await owner_conn.close()


async def add_columns_safely(
    session,
    *,
    table: str,
    columns: list[dict],
    owner_url_fn,
) -> list[str]:
    """Convenience: apply many DDLs in sequence using add_column_safely.

    Each item in `columns` is `{"column": str, "ddl": str}`. Returns a
    list of result strings in order ("session"|"owner"|"skipped").

    Use this when adding several columns to one table — saves the
    boilerplate loop in the caller.
    """
    out: list[str] = []
    for c in columns:
        result = await add_column_safely(
            session,
            table=table,
            column=c["column"],
            ddl=c["ddl"],
            owner_url_fn=owner_url_fn,
        )
        out.append(result)
    return out
