"""Ingest Discord channel history into events table.

For local dev: reads a JSON dump (mock format) at scripts/fixtures/discord_dump.json.
For production: pull via Mother bot's MCP plugin OR Discord API directly.

The two-step ingest pattern:
  1. raw event into `events` (immediate, every message)
  2. classifier picks decision/action/question events for `decisions`/`actions`/etc.
"""
from __future__ import annotations
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import text

SCRIPT = Path(__file__).resolve()
sys.path.insert(0, str(SCRIPT.parents[1]))

from lab_lib.db import service_session  # noqa: E402
from lab_lib.logging import configure_logging, get_logger  # noqa: E402
from lab_lib.settings import settings  # noqa: E402

configure_logging()
log = get_logger("discord_ingest")

FIXTURE = SCRIPT.parent / "fixtures" / "discord_dump.json"


async def get_default_tenant() -> str:
    async with service_session() as s:
        r = await s.execute(text("SELECT id FROM tenants WHERE slug = :s"),
                            {"s": settings.default_tenant_slug})
        row = r.first()
        if not row:
            raise RuntimeError("default tenant missing — run `make seed`")
        return str(row[0])


async def get_member_id(tenant_id: str, external_id: str) -> str | None:
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT id FROM members WHERE tenant_id = :tid AND external_id = :eid
        """), {"tid": tenant_id, "eid": external_id})
        row = r.first()
        return str(row[0]) if row else None


async def ingest_messages(tenant_id: str, messages: list[dict]) -> int:
    n = 0
    async with service_session() as s:
        await s.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id})
        for m in messages:
            mid = await get_member_id(tenant_id, m.get("author_id", ""))
            await s.execute(text("""
                INSERT INTO events (tenant_id, source, kind, member_id, payload, ts)
                VALUES (current_tenant_id(), 'discord', :kind, :mid, CAST(:payload AS jsonb), :ts)
            """), {
                "kind": m.get("kind", "message"),
                "mid": mid,
                "payload": json.dumps(m, default=str),
                "ts": m.get("ts") or datetime.now(timezone.utc).isoformat(),
            })
            n += 1
    return n


async def main():
    if not FIXTURE.exists():
        log.warning("discord.no_fixture", path=str(FIXTURE))
        log.info("discord.create_fixture_template")
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps([
            {
                "kind": "message",
                "channel": "daily-research",
                "channel_id": "1468135779271180502",
                "author_id": "1186944844401225808",
                "author_name": "Jay",
                "content": "Sediment MVP scaffolding 시작 — 모든 phase local로 진행",
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        ], indent=2, ensure_ascii=False))
        log.info("discord.fixture_written", path=str(FIXTURE))
        return

    tid = await get_default_tenant()
    msgs = json.loads(FIXTURE.read_text())
    n = await ingest_messages(tid, msgs)
    log.info("discord.ingest_done", count=n)


if __name__ == "__main__":
    asyncio.run(main())
