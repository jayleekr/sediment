"""Slack webhook transport (placeholder).

Slack incoming webhooks accept a JSON body with `text` field. Markdown is
supported in `text` with Slack's mrkdwn subset. The Discord-format markdown
will mostly render but bold/link syntax differs.

NOT WIRED YET — placeholder to prove the transport plugin shape. When the
first tenant wants Slack delivery, finish this + add slack-friendly
templates if needed.
"""
from __future__ import annotations
import json
import httpx


async def send(url: str, content: str) -> tuple[int, str]:
    if len(content) > 39_000:
        content = content[:39_000] + "\n…(truncated)"
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.post(url, content=json.dumps({"text": content}),
                         headers={"Content-Type": "application/json"})
    return r.status_code, (r.text or "")[:300] if r.status_code >= 300 else ""
