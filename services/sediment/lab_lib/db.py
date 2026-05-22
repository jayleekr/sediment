"""Async DB session + tenant context.

Two roles:
  - app: subject to RLS. SET LOCAL app.tenant_id required.
  - service: BYPASSRLS. Used for cron/ingest/admin.
"""
from __future__ import annotations
from contextlib import asynccontextmanager
from typing import AsyncIterator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .settings import settings

# Two engines — separate to enforce role at connection level.
_engine_app = create_async_engine(settings.database_url_app, pool_pre_ping=True, pool_size=5, max_overflow=10)
_engine_service = create_async_engine(settings.database_url_service, pool_pre_ping=True, pool_size=2, max_overflow=4)

SessionApp = async_sessionmaker(_engine_app, expire_on_commit=False, class_=AsyncSession)
SessionService = async_sessionmaker(_engine_service, expire_on_commit=False, class_=AsyncSession)


_BYPASSRLS_WARNED = False


@asynccontextmanager
async def app_session(tenant_id: str) -> AsyncIterator[AsyncSession]:
    """RLS-enforced session. Sets app.tenant_id at connection level.

    Defense-in-depth (sediment#16): logs ONCE at WARN level when the
    connection is authenticated as a BYPASSRLS role. RLS policies are
    silently ignored for such roles → cross-tenant leak risk. The fix is
    Supabase-side (create curator_app role NOT BYPASSRLS + point
    DATABASE_URL_APP at it); this warning surfaces the misconfig so it
    can't be silent. Every chat-path SQL already has an explicit
    tenant_id filter as the actual leak guard.
    """
    global _BYPASSRLS_WARNED
    async with SessionApp() as session:
        if not _BYPASSRLS_WARNED:
            try:
                r = await session.execute(text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"))
                row = r.first()
                if row and row[0]:
                    import logging
                    logging.getLogger("lab_lib.db").warning(
                        "app_session.bypassrls_detected",
                        extra={"role": "current_user", "issue": "sediment#16"},
                    )
                    _BYPASSRLS_WARNED = True
            except Exception:
                # `current_user` resolve could fail on some pooler configs;
                # don't break the session over a diagnostic check
                _BYPASSRLS_WARNED = True
        await session.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_id)})
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def service_session() -> AsyncIterator[AsyncSession]:
    """BYPASSRLS session — for ingest, cron, admin. Use sparingly."""
    async with SessionService() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def health_check() -> bool:
    async with SessionService() as s:
        r = await s.execute(text("SELECT 1"))
        return r.scalar_one() == 1


async def close_engines() -> None:
    await _engine_app.dispose()
    await _engine_service.dispose()
