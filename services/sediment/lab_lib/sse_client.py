"""Async Server-Sent Events parser + streamer.

Three places in our codebase reinvented the same `buf.split("\\n\\n")` +
scan-event/data-lines parser: accuracy_check.py, chat_smoke_kids_edu.py,
the (now removed) discord_gateway_runner.py. Per sediment#48.

This module exposes two layers:

  parse_sse_chunks(chunks)   — pure: takes an async iter of text chunks,
                                yields (event, data) tuples. No HTTP.
                                Easy to unit-test with a hand-built async iter.

  stream_sse(method, url, ...) — convenience: opens an httpx async stream
                                  and yields parsed events. Used by the
                                  smoke runners.

The `data` value is JSON-decoded when valid JSON, falls back to raw str
otherwise. Multi-line `data:` lines are joined with newlines (matches
the W3C SSE spec). `data: [DONE]` terminates the stream cleanly.
"""
from __future__ import annotations

import json
from typing import AsyncIterator, Any

import httpx


async def parse_sse_chunks(
    chunks: AsyncIterator[str],
) -> AsyncIterator[tuple[str, Any]]:
    """Pure SSE parser. Consumes text chunks, yields (event, data) tuples.

    Implementation notes:
    - Event default is "message" if no `event:` line in the frame (W3C spec)
    - Multi-line `data:` is joined with "\n" (W3C spec)
    - Frames separated by double-newline ("\n\n")
    - `data: [DONE]` ends the stream (chat protocol convention)
    - Invalid JSON in `data:` yields the raw string (not an error)

    Caller is responsible for closing the underlying transport.
    """
    buf = ""
    async for chunk in chunks:
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
            data_str = "\n".join(data_lines)
            if not data_str:
                continue
            if data_str == "[DONE]":
                return
            try:
                payload: Any = json.loads(data_str)
            except (ValueError, json.JSONDecodeError):
                payload = data_str
            yield evt, payload


async def stream_sse(
    method: str,
    url: str,
    *,
    json: dict | None = None,
    headers: dict | None = None,
    timeout_s: float = 60.0,
    client: httpx.AsyncClient | None = None,
) -> AsyncIterator[tuple[str, Any]]:
    """Open an SSE endpoint and yield parsed (event, data) tuples.

    If `client` is provided, the caller owns its lifecycle (we just use
    it). Otherwise a one-shot httpx.AsyncClient is created and closed
    when the generator is fully consumed or garbage collected.

    Raises httpx.HTTPStatusError on non-2xx response (before yielding).
    """
    owned_client = client is None
    c = client or httpx.AsyncClient()
    try:
        async with c.stream(
            method, url,
            json=json,
            headers=headers,
            timeout=httpx.Timeout(timeout_s, connect=10.0),
        ) as r:
            r.raise_for_status()
            async for event, data in parse_sse_chunks(r.aiter_text()):
                yield event, data
    finally:
        if owned_client:
            await c.aclose()


def extract_v(payload: Any) -> Any:
    """Convenience: most of our SSE events use {"v": <value>, "metadata": {...}}
    shape. Pull out the `v` field if it's a dict with that key; else
    return payload as-is.

    Saves the same 3-line dict-walk in every caller.
    """
    if isinstance(payload, dict) and "v" in payload:
        return payload["v"]
    return payload
