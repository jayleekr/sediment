"""Discord connector — direct HTTP API, no MCP dependency.

Talks to Discord's REST API using a bot token. Suitable for scheduled
ingest (cron) and one-off backfills. The bot must be a member of the
guild and have the `View Channels` + `Read Message History` permissions
on each channel.

Token sources (in priority order):
  1. constructor arg `token`
  2. env DISCORD_BOT_TOKEN
  3. env HYPEPROOF_DISCORD_BOT_TOKEN

Rate limiting:
  Discord caps GET /channels/{id}/messages at ~5 req/sec per route. We
  honor 429 responses with `Retry-After` and use the `X-RateLimit-Reset`
  header proactively. For backfill, set `limit=100` (max) and step the
  `before` cursor.

Watermark:
  Discord message snowflakes encode timestamps. Caller passes the last
  seen message_id as `after_external_id`; we use it as the `after`
  query param. Discord returns oldest-first when `after` is set.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from .base import ConnectorABC, NormalizedEvent, Resource


_DISCORD_BASE = "https://discord.com/api/v10"
_USER_AGENT = "Sediment-Collector/0.1 (+https://sediment.hypeproof-ai.xyz)"


def _resolve_token(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    for envvar in ("DISCORD_BOT_TOKEN", "HYPEPROOF_DISCORD_BOT_TOKEN"):
        v = os.environ.get(envvar)
        if v:
            return v
    raise RuntimeError(
        "Discord bot token not found. Set DISCORD_BOT_TOKEN or "
        "HYPEPROOF_DISCORD_BOT_TOKEN."
    )


def _snowflake_to_ts(snowflake: str) -> datetime:
    """Discord snowflake → UTC datetime.

    Format: 42 bits of (ms since Discord epoch 2015-01-01) << 22 || other.
    Used as a fallback when the message body doesn't include a clean
    timestamp; the upstream API does include `timestamp` ISO-8601 so this
    is just a defensive helper for tests / debug printouts.
    """
    discord_epoch_ms = 1420070400000  # 2015-01-01T00:00:00Z
    sf = int(snowflake)
    ms = (sf >> 22) + discord_epoch_ms
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


class DiscordConnector(ConnectorABC):
    source_name = "discord"

    def __init__(
        self,
        token: str | None = None,
        *,
        guild_id: str | None = None,
        http_timeout: float = 30.0,
    ) -> None:
        self._token = _resolve_token(token)
        self._guild_id = guild_id
        self._client = httpx.AsyncClient(
            base_url=_DISCORD_BASE,
            timeout=http_timeout,
            headers={
                "Authorization": f"Bot {self._token}",
                "User-Agent": _USER_AGENT,
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # -----------------------------------------------------------------
    # Rate-limit-aware request helper
    # -----------------------------------------------------------------

    async def _request(self, method: str, path: str, **kw) -> httpx.Response:
        for attempt in range(5):
            r = await self._client.request(method, path, **kw)
            if r.status_code == 429:
                # Discord rate limit. Honor Retry-After (seconds, may be float).
                retry = float(r.headers.get("Retry-After", "1"))
                await asyncio.sleep(min(retry + 0.05, 30.0))
                continue
            return r
        # All retries exhausted — return the last response so caller decides.
        return r

    # -----------------------------------------------------------------
    # ConnectorABC implementation
    # -----------------------------------------------------------------

    async def list_resources(self) -> list[Resource]:
        """List channels visible to the bot.

        If `guild_id` was provided, lists that guild's channels (one API
        call). Otherwise enumerates `/users/@me/guilds` and lists channels
        per guild — slower but works for multi-tenant setups.
        """
        out: list[Resource] = []
        if self._guild_id:
            guilds = [{"id": self._guild_id, "name": "(provided)"}]
        else:
            r = await self._request("GET", "/users/@me/guilds")
            if r.status_code != 200:
                raise RuntimeError(
                    f"failed to list guilds: {r.status_code} {r.text[:200]}"
                )
            guilds = r.json()

        for g in guilds:
            gid = g["id"]
            r = await self._request("GET", f"/guilds/{gid}/channels")
            if r.status_code != 200:
                # Bot may not have access to this guild's channels — skip.
                continue
            for c in r.json():
                # Only text channels (type 0) and threads (10/11/12). Voice
                # (2), stages (13), categories (4), forums (15) skipped.
                if c.get("type") not in (0, 10, 11, 12):
                    continue
                out.append(Resource(
                    id=c["id"],
                    name=c.get("name") or "(unnamed)",
                    kind="channel" if c["type"] == 0 else "thread",
                    extra={
                        "guild_id": gid,
                        "guild_name": g.get("name"),
                        "topic": c.get("topic"),
                        "type": c["type"],
                        "parent_id": c.get("parent_id"),
                    },
                ))
        return out

    async def fetch_since(
        self,
        resource: Resource,
        after_external_id: str | None,
        limit: int = 100,
    ) -> list[NormalizedEvent]:
        """Pull messages newer than `after_external_id` from `resource`.

        Returns oldest-first so the caller can advance the watermark to
        the last item's external_id. Caps at 100 per call (Discord's max).
        If no watermark, fetches the most recent `limit` messages.
        """
        params: dict[str, Any] = {"limit": min(max(1, limit), 100)}
        if after_external_id:
            params["after"] = after_external_id

        r = await self._request(
            "GET", f"/channels/{resource.id}/messages", params=params,
        )
        if r.status_code != 200:
            raise RuntimeError(
                f"discord fetch failed: {r.status_code} {r.text[:200]}"
            )
        raw = r.json()
        # Discord returns newest-first when `before` is used and oldest-first
        # when `after` is used. With `after` we get oldest-first already; for
        # the no-watermark case (most recent N), reverse so callers always
        # see oldest-first and can advance the watermark consistently.
        if not after_external_id:
            raw = list(reversed(raw))

        events: list[NormalizedEvent] = []
        for m in raw:
            # Skip bot messages (including our own) — they're not user signal.
            # Tenant config may override this later (some tenants want bot
            # outputs as evidence, e.g., GitHub Action posts).
            author = m.get("author") or {}
            if author.get("bot") and not author.get("system"):
                continue

            content = m.get("content") or ""
            ts = m.get("timestamp")
            try:
                ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                ts_dt = _snowflake_to_ts(m["id"])

            payload = {
                # Core fields the distill strategies expect
                "channel": resource.name,
                "channel_id": resource.id,
                "author": author.get("global_name") or author.get("username") or "?",
                "author_id": author.get("id"),
                "content": content,
                "message_id": m["id"],
                "ts": ts_dt.isoformat(),
                # Source-specific extras (for traceback + later use)
                "guild_id": resource.extra.get("guild_id"),
                "guild_name": resource.extra.get("guild_name"),
                "attachments": [
                    {
                        "id": a.get("id"),
                        "filename": a.get("filename"),
                        "url": a.get("url"),
                        "content_type": a.get("content_type"),
                        "size": a.get("size"),
                    }
                    for a in (m.get("attachments") or [])
                ],
                "reply_to": (m.get("referenced_message") or {}).get("id"),
                "edited_at": m.get("edited_timestamp"),
            }
            events.append(NormalizedEvent(
                source="discord",
                kind="message",
                external_id=m["id"],
                ts=ts_dt,
                payload=payload,
                member_external_id=author.get("id"),
                resource_id=resource.id,
            ))
        return events


# ---------------------------------------------------------------------------
# Convenience: one-shot fetch (used by scripts/discord_fetch.py)
# ---------------------------------------------------------------------------

async def fetch_channel_messages(
    channel_id: str,
    *,
    channel_name: str = "",
    limit: int = 100,
    after_external_id: str | None = None,
    token: str | None = None,
) -> list[NormalizedEvent]:
    """Single-shot helper for ad-hoc backfills / dogfood runs.

    Usage:
        events = await fetch_channel_messages(
            channel_id="1468135779271180502",
            channel_name="daily-research",
            limit=50,
        )
    """
    conn = DiscordConnector(token=token)
    try:
        res = Resource(
            id=channel_id,
            name=channel_name or channel_id,
            kind="channel",
            extra={},
        )
        return await conn.fetch_since(res, after_external_id, limit=limit)
    finally:
        await conn.aclose()
