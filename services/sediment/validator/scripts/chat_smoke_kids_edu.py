"""End-to-end chat smoke for the kids-edu tenant.

Validates the entire post-ingest pipeline against prod:
  1. mint a kids-edu dev token (Jay's email is seeded as admin)
  2. create a conversation
  3. POST /v1/sediment/stream with the canonical prompt
  4. parse SSE, collect citations, await [DONE]
  5. assert >= 1 citation AND at least one cites the AI Native assets file

Pass = exits 0. Fail = exits 2 with a diff describing what was missing.
No browser, no Playwright — pure HTTP. Run anywhere.

Env:
  SEDIMENT_API       https://hypeproof-sediment.fly.dev (default)
  KIDS_EDU_EMAIL     jayleekr0125@gmail.com (default — seeded admin)
  CHAT_QUERY         "AI Native 마인드 7종 설명해줘" (default)
  EXPECT_REF         partial substring required in at least one citation ref
                     (default: "ai-native-assets")
"""
from __future__ import annotations
import asyncio
import json
import os
import sys

import httpx

API = os.environ.get("SEDIMENT_API", "https://hypeproof-sediment.fly.dev")
EMAIL = os.environ.get("KIDS_EDU_EMAIL", "jayleekr0125@gmail.com")
QUERY = os.environ.get("CHAT_QUERY", "AI Native 마인드 7종 설명해줘")
EXPECT = os.environ.get("EXPECT_REF", "ai-native-assets")
TIMEOUT_S = float(os.environ.get("CHAT_TIMEOUT_S", "60"))


async def _mint(client: httpx.AsyncClient) -> str:
    r = await client.post(f"{API}/api/v1/auth/dev-token", json={"email": EMAIL})
    r.raise_for_status()
    return r.json()["token"]


async def _new_conv(client: httpx.AsyncClient, token: str) -> str:
    r = await client.post(
        f"{API}/api/v1/conversations",
        json={"title": "kids-edu-smoke"},
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()
    return r.json()["id"]


async def _save_user_msg(client: httpx.AsyncClient, token: str, conv_id: str, q: str) -> None:
    r = await client.post(
        f"{API}/api/v1/conversations/{conv_id}/messages",
        json={"content": q, "role": "user"},
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()


async def _stream(client: httpx.AsyncClient, token: str, conv_id: str, q: str) -> dict:
    """Parse the SSE stream; return {'citations': [...], 'answer': str, 'events': int}."""
    citations: list[dict] = []
    answer_chunks: list[str] = []
    events = 0
    async with client.stream(
        "POST", f"{API}/v1/sediment/stream",
        json={"conv_id": conv_id, "query": q},
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(TIMEOUT_S, connect=10.0),
    ) as r:
        r.raise_for_status()
        buf = ""
        current_event = "message"
        async for chunk in r.aiter_text():
            buf += chunk
            while "\n\n" in buf:
                frame, buf = buf.split("\n\n", 1)
                events += 1
                evt = "message"
                data_lines: list[str] = []
                for line in frame.split("\n"):
                    if line.startswith("event: "):
                        evt = line[7:].strip()
                    elif line.startswith("data: "):
                        data_lines.append(line[6:])
                current_event = evt
                data = "\n".join(data_lines)
                if not data or data == "[DONE]":
                    continue
                try:
                    j = json.loads(data)
                except Exception:
                    continue
                # Server emits SSE frames as {"v": <payload>, "metadata": {...}}
                # — citation payload is the citation dict; delta payload is the
                # token string. See applications/sediment_langgraph/main.py
                # `_sse(event, {"v": ...})`.
                payload = j.get("v") if isinstance(j, dict) else j
                if evt == "citation":
                    if isinstance(payload, dict):
                        citations.append(payload)
                elif evt == "delta":
                    if isinstance(payload, str):
                        answer_chunks.append(payload)
    return {
        "citations": citations,
        "answer": "".join(answer_chunks),
        "events": events,
    }


async def main() -> int:
    print(f"== kids-edu chat smoke ==")
    print(f"API:    {API}")
    print(f"EMAIL:  {EMAIL}")
    print(f"QUERY:  {QUERY!r}")
    print(f"EXPECT: ref contains {EXPECT!r}")
    print()
    async with httpx.AsyncClient(timeout=30) as c:
        try:
            token = await _mint(c)
            conv_id = await _new_conv(c, token)
            print(f"conv_id: {conv_id}")
            await _save_user_msg(c, token, conv_id, QUERY)
            result = await _stream(c, token, conv_id, QUERY)
        except httpx.HTTPStatusError as e:
            print(f"FATAL http {e.response.status_code}: {e.response.text[:500]}", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"FATAL {type(e).__name__}: {e}", file=sys.stderr)
            return 1

    cits = result["citations"]
    ans = result["answer"]
    print(f"events:    {result['events']}")
    print(f"citations: {len(cits)}")
    for i, c in enumerate(cits[:5], 1):
        ref = c.get("ref") or "?"
        ctype = c.get("type", "?")
        print(f"  [{i}] {ref} ({ctype})")
    if len(cits) > 5:
        print(f"  ...({len(cits)-5} more)")
    print(f"\nanswer (first 240 chars):\n  {ans[:240]!r}")

    # Assertions
    if len(cits) == 0:
        print("\nFAIL: zero citations — retrieval landed nothing", file=sys.stderr)
        return 2
    matched = [c for c in cits if EXPECT in (c.get("ref") or "")]
    if not matched:
        print(f"\nFAIL: no citation contained {EXPECT!r}", file=sys.stderr)
        print("    got refs:", [c.get("ref") for c in cits], file=sys.stderr)
        return 2
    if not ans.strip():
        print("\nFAIL: empty answer body", file=sys.stderr)
        return 2

    print(f"\nOK: {len(cits)} citations, {len(matched)} match {EXPECT!r}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
