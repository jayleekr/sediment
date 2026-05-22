"""Discord Gateway listener — real-time MESSAGE_CREATE.

Companion to `discord.py` (the REST poller). The poller is for catch-up
+ history; this is for sub-second @mention reply (CLI feels async, web
feels async; chat reply must feel live or users disengage).

This is a thin asyncio wrapper over `websockets` — no `discord.py`
dependency (keeps the image lean + matches our existing httpx-direct
style for connectors).

Gateway op codes we handle:
  10 HELLO    — server tells us heartbeat interval
   2 IDENTIFY — we send token + intents
   1 HEARTBEAT — we send periodically (and respond to op 1 from server)
  11 HEARTBEAT_ACK — server confirms our heartbeat
   0 DISPATCH — events; we care about t="MESSAGE_CREATE"
   7 RECONNECT — server asks us to reconnect (RESUME)
   9 INVALID_SESSION — full re-IDENTIFY required
   6 RESUME (we send) — after disconnect, with session_id + last seq

Intents we declare (privileged — see Discord developer portal):
  GUILDS           = 1 << 0  (channel/guild metadata in payloads)
  GUILD_MESSAGES   = 1 << 9  (MESSAGE_CREATE in guilds)
  MESSAGE_CONTENT  = 1 << 15 (priv) — REQUIRED so message.content is non-empty
  DIRECT_MESSAGES  = 1 << 12

Without MESSAGE_CONTENT enabled in the bot's portal, `content` arrives
empty and we can't extract the question text. The portal toggle is a
Jay action — see sediment#TBD onboarding note.

Yields NormalizedEvent objects ready for `decide()` consumption. The
orchestrator (scripts/discord_gateway_runner.py) loops these and
dispatches based on the agent's decision.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from typing import AsyncIterator, Any

import httpx
import websockets

from .base import NormalizedEvent
from ..logging import get_logger

log = get_logger("discord_gateway")

_DISCORD_API = "https://discord.com/api/v10"
# Intents bitmask: GUILDS | GUILD_MESSAGES | MESSAGE_CONTENT | DIRECT_MESSAGES
INTENTS = (1 << 0) | (1 << 9) | (1 << 15) | (1 << 12)


class DiscordGateway:
    """Long-running Discord Gateway client.

    Usage:
        gw = DiscordGateway(token=...)
        async for event in gw.stream():
            await handle(event)

    The stream() generator runs forever — wrap caller in supervisor /
    reconnect-on-exception. Internal disconnects are handled (RESUME
    + HEARTBEAT_ACK timeout), but a full process crash needs supervisord.
    """

    def __init__(self, token: str, *, user_agent: str = "Sediment-Gateway/0.1") -> None:
        self._token = token
        self._user_agent = user_agent
        # Session state for RESUME
        self._session_id: str | None = None
        self._sequence: int | None = None
        self._resume_gateway_url: str | None = None
        # Bot identity (filled after READY) — used to detect @-mentions of self
        self._bot_user_id: str | None = None
        self._bot_username: str | None = None

    @property
    def bot_user_id(self) -> str | None:
        return self._bot_user_id

    async def _fetch_gateway_url(self) -> str:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(
                f"{_DISCORD_API}/gateway/bot",
                headers={
                    "Authorization": f"Bot {self._token}",
                    "User-Agent": self._user_agent,
                },
            )
            r.raise_for_status()
            return r.json()["url"] + "?v=10&encoding=json"

    async def stream(self) -> AsyncIterator[NormalizedEvent]:
        """Connect, identify, yield NormalizedEvents as messages arrive.

        Reconnect-on-disconnect with exponential backoff (capped 60s).
        Each reconnect attempts RESUME if session_id is known; otherwise
        falls back to full IDENTIFY. Yields events from BOTH paths.
        """
        backoff = 1.0
        while True:
            try:
                url = self._resume_gateway_url or await self._fetch_gateway_url()
                log.info("gateway.connect", url=url[:60], will_resume=bool(self._session_id))
                async with websockets.connect(url, max_size=2**20) as ws:
                    async for ev in self._session(ws):
                        yield ev
                    log.info("gateway.session.ended_normally")
                backoff = 1.0  # successful clean exit resets backoff
            except (websockets.exceptions.ConnectionClosed,
                    websockets.exceptions.WebSocketException,
                    httpx.HTTPError,
                    asyncio.TimeoutError) as e:
                log.warning("gateway.disconnect", err=str(e)[:200],
                            backoff_s=backoff,
                            will_resume=bool(self._session_id))
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
            except Exception as e:
                # Unknown error — reset session to be safe + log loudly
                log.exception("gateway.unexpected_error", err=str(e)[:200])
                self._session_id = None
                self._resume_gateway_url = None
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    async def _session(self, ws) -> AsyncIterator[NormalizedEvent]:
        # 1) Receive HELLO
        hello = await asyncio.wait_for(ws.recv(), timeout=30.0)
        hello_msg = json.loads(hello)
        if hello_msg.get("op") != 10:
            raise RuntimeError(f"expected HELLO op 10, got {hello_msg.get('op')}")
        heartbeat_interval = hello_msg["d"]["heartbeat_interval"] / 1000.0
        log.info("gateway.hello", heartbeat_interval_s=heartbeat_interval)

        # 2) IDENTIFY or RESUME
        if self._session_id and self._sequence is not None:
            await ws.send(json.dumps({
                "op": 6,
                "d": {
                    "token": self._token,
                    "session_id": self._session_id,
                    "seq": self._sequence,
                },
            }))
            log.info("gateway.resume.sent", session=self._session_id[:8],
                     seq=self._sequence)
        else:
            await ws.send(json.dumps({
                "op": 2,
                "d": {
                    "token": self._token,
                    "intents": INTENTS,
                    "properties": {
                        "os": "linux",
                        "browser": "sediment-gateway",
                        "device": "sediment-gateway",
                    },
                },
            }))
            log.info("gateway.identify.sent", intents=INTENTS)

        # 3) Heartbeat coroutine
        heartbeat_task = asyncio.create_task(
            self._heartbeat(ws, heartbeat_interval)
        )

        try:
            async for raw in ws:
                msg = json.loads(raw)
                op = msg.get("op")
                if msg.get("s") is not None:
                    self._sequence = msg["s"]

                if op == 0:  # DISPATCH
                    t = msg.get("t")
                    d = msg.get("d") or {}
                    if t == "READY":
                        self._session_id = d.get("session_id")
                        self._resume_gateway_url = (
                            d.get("resume_gateway_url", "") + "?v=10&encoding=json"
                        )
                        user = d.get("user") or {}
                        self._bot_user_id = user.get("id")
                        self._bot_username = user.get("username")
                        log.info("gateway.ready", bot_id=self._bot_user_id,
                                 bot_username=self._bot_username,
                                 session=self._session_id and self._session_id[:8])
                    elif t == "RESUMED":
                        log.info("gateway.resumed.ok")
                    elif t == "MESSAGE_CREATE":
                        ev = self._normalize(d)
                        if ev is not None:
                            yield ev
                    # other dispatches (GUILD_CREATE, TYPING_START, etc.) ignored
                elif op == 1:  # HEARTBEAT (server wants one now)
                    await ws.send(json.dumps({"op": 1, "d": self._sequence}))
                elif op == 7:  # RECONNECT
                    log.info("gateway.reconnect_requested")
                    break  # session loop exits → outer reconnect
                elif op == 9:  # INVALID_SESSION
                    log.warning("gateway.invalid_session",
                                resumable=msg.get("d", False))
                    if not msg.get("d", False):
                        self._session_id = None
                        self._resume_gateway_url = None
                    await asyncio.sleep(1 + (time.time() % 4))  # jitter
                    break
                elif op == 11:  # HEARTBEAT_ACK
                    pass  # noted
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

    async def _heartbeat(self, ws, interval_s: float) -> None:
        """Send a heartbeat every `interval_s` until cancelled."""
        # Jitter the first heartbeat per Discord recommendation
        await asyncio.sleep(interval_s * 0.5)
        while True:
            try:
                await ws.send(json.dumps({"op": 1, "d": self._sequence}))
            except Exception as e:
                log.warning("gateway.heartbeat.failed", err=str(e)[:120])
                return
            await asyncio.sleep(interval_s)

    def _normalize(self, msg: dict) -> NormalizedEvent | None:
        """Discord MESSAGE_CREATE payload → NormalizedEvent.

        Returns None for messages we definitely don't want to process
        (purely for efficiency — `decide()` would skip them anyway, but
        skipping early saves a queue insertion).
        """
        author = msg.get("author") or {}
        is_bot = bool(author.get("bot")) or bool(author.get("system"))
        content = msg.get("content") or ""

        is_self_mention = False
        if self._bot_user_id:
            for mention in (msg.get("mentions") or []):
                if mention.get("id") == self._bot_user_id:
                    is_self_mention = True
                    break

        # Channel name not in MESSAGE_CREATE payload; we leave it blank and
        # let the orchestrator fill in from a guild→channel cache, OR the
        # decide() rules that don't depend on channel name still fire.
        ts_str = msg.get("timestamp")
        try:
            ts = (datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                  if ts_str else datetime.now(tz=timezone.utc))
        except (AttributeError, ValueError):
            ts = datetime.now(tz=timezone.utc)

        return NormalizedEvent(
            source="discord",
            kind="message",
            external_id=msg["id"],
            ts=ts,
            payload={
                "content": content,
                "author": author.get("global_name") or author.get("username") or "?",
                "author_id": author.get("id"),
                "channel_id": msg.get("channel_id"),
                "guild_id": msg.get("guild_id"),
                "message_id": msg["id"],
                "is_bot": is_bot,
                "is_bot_mention": is_self_mention,
                # Reply needs these for posting back as a thread/reply
                "reply_to": (msg.get("referenced_message") or {}).get("id"),
            },
            member_external_id=author.get("id"),
            resource_id=msg.get("channel_id"),
        )


def from_env() -> "DiscordGateway":
    """Construct from env. Token sources in priority order:
       1. DISCORD_BOT_TOKEN  2. HYPEPROOF_DISCORD_BOT_TOKEN"""
    for envvar in ("DISCORD_BOT_TOKEN", "HYPEPROOF_DISCORD_BOT_TOKEN"):
        v = os.environ.get(envvar)
        if v:
            return DiscordGateway(token=v)
    raise RuntimeError(
        "Discord bot token not found. Set DISCORD_BOT_TOKEN or "
        "HYPEPROOF_DISCORD_BOT_TOKEN."
    )
