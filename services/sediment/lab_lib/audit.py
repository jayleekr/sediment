"""Append-only per-call audit log → mcp_call_log table.

Fire-and-forget: callers use `audit_log(...)` and continue; the write is
scheduled as a task. Failure to log must never break the request, so all
errors are swallowed and counted.
"""
from __future__ import annotations
import asyncio
import logging
from typing import Optional

from sqlalchemy import text

from .db import service_session

log = logging.getLogger(__name__)


async def _write(tenant_id: str, member_id: str, tool: str, client: Optional[str],
                 latency_ms: int, result_bytes: int, status_code: int,
                 error_reason: Optional[str]) -> None:
    try:
        async with service_session() as s:
            await s.execute(text("""
                INSERT INTO mcp_call_log
                  (tenant_id, member_id, tool, client,
                   latency_ms, result_bytes, status_code, error_reason)
                VALUES
                  (CAST(:tenant AS uuid), CAST(:member AS uuid),
                   :tool, :client, :latency, :rb, :sc, :err)
            """), {
                "tenant": tenant_id, "member": member_id,
                "tool": tool, "client": client,
                "latency": latency_ms, "rb": result_bytes,
                "sc": status_code, "err": error_reason,
            })
            await s.commit()
    except Exception as e:
        log.warning("audit_log write failed: %s", e)


def audit_log(tenant_id: str, member_id: str, tool: str,
              latency_ms: int, *, client: Optional[str] = None,
              result_bytes: int = 0, status_code: int = 200,
              error_reason: Optional[str] = None) -> None:
    """Fire-and-forget audit write. Safe to call from any async context."""
    try:
        asyncio.create_task(_write(
            tenant_id, member_id, tool, client,
            latency_ms, result_bytes, status_code, error_reason,
        ))
    except RuntimeError:
        # No running loop (e.g. sync context). Skip silently — audit log
        # is best-effort.
        pass
