"""Daily retro cron — 22:00.

Compacts the day's events into a per-tenant summary artifact.
Lighter than dream — just summarization, no extraction.
"""
from __future__ import annotations
import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lab_lib.db import service_session  # noqa: E402
from lab_lib.logging import configure_logging, get_logger  # noqa: E402

configure_logging()
log = get_logger("retro")


async def collect_day(tenant_id: str) -> dict:
    async with service_session() as s:
        await s.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id})
        msg_count = (await s.execute(text("""
            SELECT count(*) FROM messages
            WHERE ts >= current_date AND ts < current_date + interval '1 day'
        """))).scalar_one()
        evt_count = (await s.execute(text("""
            SELECT count(*) FROM events
            WHERE ts >= current_date AND ts < current_date + interval '1 day'
        """))).scalar_one()
        new_artifacts = (await s.execute(text("""
            SELECT count(*) FROM artifacts
            WHERE created_at >= current_date AND created_at < current_date + interval '1 day'
        """))).scalar_one()
    return {"messages": msg_count, "events": evt_count, "new_artifacts": new_artifacts}


async def main():
    async with service_session() as s:
        r = await s.execute(text("SELECT id::text, slug FROM tenants WHERE status='active'"))
        tenants = [dict(row._mapping) for row in r]

    out = []
    for t in tenants:
        d = await collect_day(t["id"])
        out.append({"tenant": t["slug"], **d})
        log.info("retro.tenant", slug=t["slug"], **d)

    print(json.dumps({"status": "ok", "results": out}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
