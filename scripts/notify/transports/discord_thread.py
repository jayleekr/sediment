"""Discord thread reply transport — posts a chat-answer back to the
ORIGINAL channel as a reply to the @-mention message.

Differs from `discord_webhook.py`:
  - Webhook: outbound notification, fire-and-forget, posts as a fixed
    persona to a fixed channel. No reply context.
  - Thread reply: uses the bot's REST identity (Bot token), posts via
    POST /channels/{channel_id}/messages with `message_reference`
    pointing at the original message. The Discord UI renders this as a
    quoted reply with a "↩" affordance.

Endpoint:
  POST https://discord.com/api/v10/channels/{channel_id}/messages
  Headers: Authorization: Bot <DISCORD_BOT_TOKEN>
  Body: {
    "content": "...",
    "message_reference": {
      "message_id": "<reply_to>",
      "channel_id": "<chan>",
      "guild_id": "<guild>",
      "fail_if_not_exists": false
    }
  }

The route key for this transport is `discord_thread:<channel_id>:<message_id>`.
We pass that as `url` from the orchestrator; the transport splits it.
The bot token comes from `DISCORD_BOT_TOKEN` env (NOT from the routes
file — it's a shared bot identity, not a per-channel webhook).
"""
from __future__ import annotations

import asyncio
import json
import os

import httpx


MAX_BODY = 1900   # Discord 2000-char ceiling minus safety margin
USER_AGENT = "hypeproof-notify/0.2 (+https://github.com/jayleekr/hypeproof-harness)"


async def send(url: str, content: str) -> tuple[int, str]:
    """Post `content` as a reply to a Discord message.

    `url` format: `discord_thread:<channel_id>:<message_id>:<guild_id>`
    Example:      `discord_thread:1506104152747671694:1234567890:99999`

    The transport returns (http_status, error_msg). 200–299 = success.
    """
    parts = url.split(":")
    if len(parts) < 3 or parts[0] != "discord_thread":
        return 0, f"discord_thread: malformed url {url[:60]}"
    channel_id = parts[1]
    message_id = parts[2]
    guild_id = parts[3] if len(parts) > 3 else None

    token = (os.environ.get("DISCORD_BOT_TOKEN")
             or os.environ.get("HYPEPROOF_DISCORD_BOT_TOKEN") or "").strip()
    if not token:
        return 0, "discord_thread: DISCORD_BOT_TOKEN not set"

    if len(content) > MAX_BODY:
        content = content[: MAX_BODY - 20] + "\n…(truncated)"

    payload: dict = {
        "content": content,
        "message_reference": {
            "message_id": message_id,
            "channel_id": channel_id,
            "fail_if_not_exists": False,
        },
        # Don't allow @everyone or role mentions in our reply (defense
        # against an injected query that asks the bot to "ping @everyone").
        "allowed_mentions": {"parse": []},
    }
    if guild_id:
        payload["message_reference"]["guild_id"] = guild_id

    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    api = f"https://discord.com/api/v10/channels/{channel_id}/messages"

    async with httpx.AsyncClient(timeout=15.0) as c:
        for attempt in range(3):
            r = await c.post(api, headers=headers, content=json.dumps(payload))
            if r.status_code == 429:
                retry_after = float(r.headers.get("Retry-After", "1"))
                await asyncio.sleep(min(retry_after + 0.1, 10.0))
                continue
            if 200 <= r.status_code < 300:
                return r.status_code, ""
            return r.status_code, (r.text or "")[:300]
    return 429, "rate-limited after 3 attempts"
