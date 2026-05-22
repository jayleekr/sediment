"""Long-running Discord Gateway runner — listens for messages, runs them
through the Collection AI Agent, and dispatches reply/ingest/notify per
the agent's decision.

This is the INTERACTIVE arm of Sediment's collection loop (sediment#16
Discord reply design):

  Discord MESSAGE_CREATE (Gateway WS)
       │
       ▼
  DiscordGateway.stream() → NormalizedEvent
       │
       ▼
  resolve_tenant(guild_id) → tenant_id + integration.config
       │
       ▼
  decide(event, config) → CollectionDecision
       │
       ├─ if ingest: INSERT INTO events (audit)
       ├─ if reply:  POST /v1/sediment/stream → discord_thread transport
       └─ (notify: rare here — usually a thread reply IS the notification)

Anti-loop guard: `decide()` filters bot messages (is_bot=True) so our
own reply doesn't trigger another reply. Belt+suspenders: we also skip
events where author_id == self bot user id.

Run via supervisord (one process per app instance — Discord Gateway
sessions are tied to the bot identity, not to tenants, so 1 listener
handles every channel the bot is in across all tenants).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve()
sys.path.insert(0, str(SCRIPT.parents[1]))

import httpx
from sqlalchemy import text

from lab_lib.collection_agent import decide  # noqa: E402
from lab_lib.connectors.discord_gateway import DiscordGateway, from_env  # noqa: E402
from lab_lib.db import service_session  # noqa: E402
from lab_lib.logging import configure_logging, get_logger  # noqa: E402

configure_logging()
log = get_logger("discord_gateway_runner")


# Default routes path. The transport (discord_thread) is bundled via the
# vendored scripts/notify/. Routes file lists which channels we'll reply in.
ROUTES_PATH = Path(__file__).resolve().parents[1] / "config" / "notify_routes.yaml"

# Backend endpoint for chat composition. Same one the web UI POSTs to.
LANGGRAPH_BASE = os.environ.get(
    "LANGGRAPH_BASE",
    f"http://127.0.0.1:{os.environ.get('SEDIMENT_LANGGRAPH_PORT', '10020')}",
)
PLATFORM_BASE = os.environ.get(
    "PLATFORM_BASE",
    f"http://127.0.0.1:{os.environ.get('SEDIMENT_PLATFORM_PORT', '10100')}",
)


async def _resolve_tenant_and_config(guild_id: str | None) -> tuple[str | None, str | None, dict]:
    """Map a Discord guild_id → (tenant_slug, tenant_id, integration.config).

    A guild can host messages for one tenant. `integrations.config.discord.guild_id`
    (when set) is the link; otherwise we fall back to the legacy `default_tenant_slug`
    so single-tenant deployments work without explicit guild mapping.
    """
    if not guild_id:
        return None, None, {}

    async with service_session() as s:
        # Look for an explicit guild → tenant mapping
        r = await s.execute(text("""
            SELECT t.slug, t.id::text, i.config
            FROM integrations i
            JOIN tenants t ON t.id = i.tenant_id
            WHERE i.kind = 'discord'
              AND (i.config->>'guild_id' = :gid OR i.config->'discord'->>'guild_id' = :gid)
            LIMIT 1
        """), {"gid": guild_id})
        row = r.first()
        if row:
            return row[0], row[1], (row[2] or {})

        # Fallback: default tenant (hypeproof-lab in current setup) — single
        # tenant deployments don't need explicit guild mapping
        r = await s.execute(text("""
            SELECT slug, id::text FROM tenants WHERE slug = 'hypeproof-lab' LIMIT 1
        """))
        row = r.first()
        if row:
            return row[0], row[1], {"source_kind": "transcript"}
    return None, None, {}


async def _mint_token_for_tenant(http: httpx.AsyncClient, tenant_slug: str) -> str | None:
    """Mint a JWT for the tenant's admin via /api/v1/auth/dev-token.

    Picks the first admin member for the tenant. This is acceptable for
    the bot-reply context: the bot acts on the tenant's behalf, and the
    composition is RLS-scoped to that tenant regardless of which admin
    identity is on the JWT.
    """
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT email FROM members
            WHERE tenant_id IN (SELECT id FROM tenants WHERE slug = :slug)
              AND role = 'admin'
            ORDER BY created_at ASC LIMIT 1
        """), {"slug": tenant_slug})
        row = r.first()
    if not row:
        log.warning("gateway.mint.no_admin", tenant=tenant_slug)
        return None
    email = row[0]
    try:
        rr = await http.post(
            f"{PLATFORM_BASE}/api/v1/auth/dev-token",
            json={"email": email},
            timeout=10.0,
        )
        if rr.status_code != 200:
            log.warning("gateway.mint.failed",
                        status=rr.status_code, body=rr.text[:120])
            return None
        return rr.json().get("token")
    except Exception as e:
        log.warning("gateway.mint.error", err=str(e)[:120])
        return None


async def _stream_answer(
    http: httpx.AsyncClient, token: str, conv_id: str, query: str,
) -> dict:
    """POST /v1/sediment/stream and return {answer, citations}."""
    citations: list[dict] = []
    answer_chunks: list[str] = []
    async with http.stream(
        "POST", f"{LANGGRAPH_BASE}/v1/sediment/stream",
        json={"conv_id": conv_id, "query": query},
        headers={"Authorization": f"Bearer {token}"},
        timeout=60.0,
    ) as r:
        r.raise_for_status()
        buf = ""
        async for chunk in r.aiter_text():
            buf += chunk
            while "\n\n" in buf:
                frame, buf = buf.split("\n\n", 1)
                evt = "message"
                data_lines: list[str] = []
                for line in frame.split("\n"):
                    if line.startswith("event: "):
                        evt = line[7:].strip()
                    elif line.startswith("data: "):
                        data_lines.append(line[6:])
                data = "\n".join(data_lines)
                if not data or data == "[DONE]":
                    continue
                try:
                    j = json.loads(data)
                except Exception:
                    continue
                payload = j.get("v") if isinstance(j, dict) else j
                if evt == "citation" and isinstance(payload, dict):
                    citations.append(payload)
                elif evt == "delta" and isinstance(payload, str):
                    answer_chunks.append(payload)
    return {"answer": "".join(answer_chunks), "citations": citations}


async def _create_conv(
    http: httpx.AsyncClient, token: str, title: str,
) -> str | None:
    """Create a conversation (per-message), get its id for the stream call."""
    try:
        r = await http.post(
            f"{PLATFORM_BASE}/api/v1/conversations",
            json={"title": title},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        r.raise_for_status()
        return r.json().get("id")
    except Exception as e:
        log.warning("gateway.conv.create_failed", err=str(e)[:120])
        return None


def _format_reply(answer: str, citations: list[dict]) -> str:
    """Format the LLM answer + a short citation footer for a Discord reply.

    Keeps under ~1700 chars (transport truncates at 1900 anyway). Footer
    lists the top 3 unique refs in plain text so the user can verify."""
    body = (answer or "(no answer composed)").strip()
    if len(body) > 1400:
        body = body[:1380] + "…(truncated)"

    refs_seen = []
    for c in citations:
        ref = c.get("ref")
        if ref and ref not in refs_seen:
            refs_seen.append(ref)
        if len(refs_seen) >= 3:
            break
    if refs_seen:
        body += "\n\n📚 sources:\n" + "\n".join(f"• {r}" for r in refs_seen)
    return body


async def _record_event(
    tenant_id: str, kind: str, payload: dict[str, Any],
) -> None:
    """Write a row to events for audit/replay (same shape as discord_fetch)."""
    try:
        async with service_session() as s:
            await s.execute(text("""
                INSERT INTO events (tenant_id, source, kind, payload, ts)
                VALUES (CAST(:tid AS uuid), 'discord', :kind, CAST(:p AS jsonb), now())
            """), {"tid": tenant_id, "kind": kind,
                   "p": json.dumps(payload, default=str)})
            await s.commit()
    except Exception as e:
        log.warning("gateway.event.insert_failed", err=str(e)[:120])


async def _dispatch_reply(
    http: httpx.AsyncClient,
    decision_query: str,
    tenant_slug: str,
    channel_id: str,
    message_id: str,
    guild_id: str | None,
) -> dict:
    """The full reply pipeline: mint token, create conv, stream answer,
    post back as a Discord thread reply."""
    token = await _mint_token_for_tenant(http, tenant_slug)
    if not token:
        return {"status": "skip_no_token"}
    conv_id = await _create_conv(http, token, f"discord-reply-{message_id}")
    if not conv_id:
        return {"status": "skip_no_conv"}

    result = await _stream_answer(http, token, conv_id, decision_query)
    reply_body = _format_reply(result["answer"], result["citations"])

    # Send via discord_thread transport (vendored from harness)
    import importlib.util
    transport_path = (
        Path(__file__).resolve().parents[2]
        / "scripts" / "notify" / "transports" / "discord_thread.py"
    )
    if not transport_path.is_file():
        log.warning("gateway.transport.missing", path=str(transport_path))
        return {"status": "skip_no_transport"}
    spec = importlib.util.spec_from_file_location("hp_discord_thread", transport_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    url = f"discord_thread:{channel_id}:{message_id}"
    if guild_id:
        url += f":{guild_id}"
    http_status, err = await mod.send(url, reply_body)
    if 200 <= http_status < 300:
        return {"status": "ok", "http": http_status, "conv_id": conv_id,
                "citation_count": len(result["citations"])}
    return {"status": "failed", "http": http_status, "err": err}


async def amain() -> int:
    log.info("gateway.runner.start",
             langgraph=LANGGRAPH_BASE, platform=PLATFORM_BASE)
    gw = from_env()
    http = httpx.AsyncClient()
    try:
        async for ev in gw.stream():
            # Belt-and-suspenders bot loop guard (decide() also filters,
            # but we want zero chance our own messages trigger DB writes)
            if gw.bot_user_id and ev.payload.get("author_id") == gw.bot_user_id:
                continue

            guild_id = ev.payload.get("guild_id")
            channel_id = ev.payload.get("channel_id")
            tenant_slug, tenant_id, cfg = await _resolve_tenant_and_config(guild_id)
            if not tenant_slug or not tenant_id:
                log.info("gateway.no_tenant_mapping", guild=guild_id)
                continue

            # Force source_kind = transcript for Discord gateway events
            # (overrides whatever the integration config might have).
            cfg = {**cfg, "source_kind": "transcript"}

            d = decide(ev, cfg)
            log.info("gateway.decided",
                     tenant=tenant_slug, kind=ev.kind,
                     ingest=d.ingest, reply=d.reply,
                     channel=channel_id, msg=ev.payload.get("message_id"),
                     rule=d.matched_rule)

            # Audit row — every gateway message gets an event, even if
            # decide() said skip. Useful for replay/debugging the rules.
            if d.ingest:
                await _record_event(tenant_id, ev.kind, ev.payload)

            if d.reply and d.reply_query:
                t0 = time.time()
                outcome = await _dispatch_reply(
                    http,
                    decision_query=d.reply_query,
                    tenant_slug=tenant_slug,
                    channel_id=channel_id,
                    message_id=ev.payload.get("message_id"),
                    guild_id=guild_id,
                )
                log.info("gateway.reply.done",
                         tenant=tenant_slug, ms=int((time.time() - t0) * 1000),
                         **{k: v for k, v in outcome.items() if not k.startswith("_")})
    finally:
        await http.aclose()
    return 0


def main() -> None:
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
