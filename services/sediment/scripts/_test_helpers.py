"""Shared helpers for smoke / accuracy / validator scripts.

Two helpers, both used by smoke runners hitting the public API:

  smoke_conv_title(base)
      Wraps conv titles in the "test:" prefix that list_convs hides by
      default. Prevents sidebar pollution. (Per sediment#46.)

  ask_question(api, email, query, ...)
      End-to-end: mint dev-token → create conv → stream answer → return
      {answer, citations, conv_id, intent}. Collapses the 60-LOC dance
      that lived in every smoke script. (Per sediment#48.)

Both helpers add the "test:" prefix automatically — callers can't forget.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import httpx

# This file lives at services/sediment/scripts/_test_helpers.py — the
# lab_lib sibling is at services/sediment/lab_lib/. Make sure imports work
# whether we're running as `python -m scripts._test_helpers` or imported
# from a sibling validator/ script that already added the parent.
_THIS = Path(__file__).resolve()
sys.path.insert(0, str(_THIS.parents[1]))

from lab_lib.sse_client import stream_sse, extract_v  # noqa: E402


_PREFIX = "test:"


def smoke_conv_title(base: str) -> str:
    """Return a conversation title that the sidebar filter will hide.

    `base` should be human-readable so a developer scanning DB rows can
    tell which script created it (`test:kids-edu-smoke`,
    `test:freshness-accuracy-2026-05-22`).

    Idempotent: if `base` already starts with the prefix, returns it
    unchanged.
    """
    if base.startswith(_PREFIX):
        return base
    return f"{_PREFIX}{base}"


async def _mint_token(
    client: httpx.AsyncClient,
    api: str,
    email: str,
    tenant_slug: str | None = None,
) -> str:
    r = await client.post(
        f"{api}/api/v1/auth/dev-token",
        json={"email": email, "tenant_slug": tenant_slug},
    )
    r.raise_for_status()
    return r.json()["token"]


async def _new_conv(
    client: httpx.AsyncClient, api: str, token: str, title: str,
) -> str:
    r = await client.post(
        f"{api}/api/v1/conversations",
        json={"title": title},
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()
    return r.json()["id"]


async def _save_user_msg(
    client: httpx.AsyncClient, api: str, token: str, conv_id: str, q: str,
) -> None:
    r = await client.post(
        f"{api}/api/v1/conversations/{conv_id}/messages",
        json={"content": q, "role": "user"},
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()


async def ask_question(
    *,
    api: str,
    email: str,
    tenant_slug: str | None = None,
    token: str | None = None,
    query: str,
    title_base: str = "smoke",
    timeout_s: float = 60.0,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """End-to-end smoke runner: returns the structured chat result.

    Use this in any smoke / accuracy script instead of hand-rolling the
    4-step (auth, conv, save, stream) dance + SSE parsing.

    Returns:
        {
            "answer":    "...assembled delta tokens...",
            "citations": [{...}, ...],
            "conv_id":   "<uuid>",
            "intent":    "library" | "elaborate" | ... | None,
            "events":    int,   # frames received (useful for diagnostics)
        }

    Title is auto-prefixed `test:` via smoke_conv_title. If `client` is
    None, an httpx.AsyncClient is created + closed; otherwise the caller
    owns its lifecycle (allows connection reuse across many ask_question
    calls in the same run).
    """
    owned_client = client is None
    c = client or httpx.AsyncClient()
    try:
        token = token or await _mint_token(c, api, email, tenant_slug)
        title = smoke_conv_title(title_base)
        conv_id = await _new_conv(c, api, token, title)
        await _save_user_msg(c, api, token, conv_id, query)

        citations: list[dict] = []
        answer_chunks: list[str] = []
        intent: str | None = None
        events = 0

        async for evt, payload in stream_sse(
            "POST", f"{api}/v1/sediment/stream",
            json={"conv_id": conv_id, "query": query},
            headers={"Authorization": f"Bearer {token}"},
            timeout_s=timeout_s,
            client=c,
        ):
            events += 1
            v = extract_v(payload)
            if evt == "citation" and isinstance(v, dict):
                citations.append(v)
            elif evt == "delta" and isinstance(v, str):
                answer_chunks.append(v)
            elif evt == "message" and isinstance(payload, dict):
                # status frames carry metadata.step + metadata.intent
                meta = payload.get("metadata") or {}
                if meta.get("step") == "router" and meta.get("intent"):
                    intent = meta["intent"]

        return {
            "answer": "".join(answer_chunks),
            "citations": citations,
            "conv_id": conv_id,
            "intent": intent,
            "events": events,
        }
    finally:
        if owned_client:
            await c.aclose()
