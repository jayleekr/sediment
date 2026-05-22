"""Discord webhook transport.

Posts markdown content to a Discord webhook URL. Honors rate-limit headers
(429 + Retry-After). Truncates body at 1900 chars (Discord's 2000-char
message ceiling minus a small safety margin).
"""
from __future__ import annotations

import asyncio
import json

import httpx


MAX_BODY = 1900
USER_AGENT = "hypeproof-notify/0.1 (+https://github.com/jayleekr/hypeproof-harness)"


async def send(url: str, content: str) -> tuple[int, str]:
    """POST content to a Discord webhook. Returns (status, err_msg)."""
    if len(content) > MAX_BODY:
        content = content[: MAX_BODY - 20] + "\n…(truncated)"

    payload = {"content": content, "username": "Sediment"}
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}

    async with httpx.AsyncClient(timeout=15.0) as c:
        for attempt in range(3):
            r = await c.post(url, headers=headers, content=json.dumps(payload))
            if r.status_code == 429:
                retry_after = float(r.headers.get("Retry-After", "1"))
                await asyncio.sleep(min(retry_after + 0.1, 10.0))
                continue
            if 200 <= r.status_code < 300:
                return r.status_code, ""
            return r.status_code, (r.text or "")[:300]
    return 429, "rate-limited after 3 attempts"
