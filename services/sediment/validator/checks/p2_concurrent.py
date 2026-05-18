"""Concurrent SSE check — fires N parallel /v1/sediment/stream requests.

Why this exists: the LangGraph service holds a single compiled GRAPH at module
scope and a MemorySaver checkpointer. Earlier versions also accumulated tokens
into a module-global list, which corrupted answers when two requests overlapped
(observed: stream A's text appeared in stream B's reply). The accumulator was
moved per-invocation, but no automated check verified that concurrent streams
stay isolated. This is that check.

Pass criteria (all must hold):
  - All N streams complete with [DONE]
  - Each stream's delta count >= 1
  - No stream's deltas appear in another stream's accumulated text
  - p95 elapsed_ms < 30s (sanity ceiling, not a SLO)
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
import time
import httpx
from pathlib import Path

# Reuse the existing SSE consumer + auth helper from p2_chat.
sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent))
from validator.checks.p2_chat import _consume_sse, _admin_token, _platform_base, _langgraph_base  # noqa: E402


# Distinct queries + their leakage *signature* — a token that the answer
# for this query is overwhelmingly likely to contain, AND that should NOT
# appear in other queries' answers.
#
# Auto-extracted signatures ("longest word in query") were too generic
# (e.g. "agent" appears in nearly every research answer); hand-pick to
# avoid false positives. Each signature is hyphenated / domain-specific.
_QUERIES: list[tuple[str, str, str]] = [
    ("ALPHA",   "최근 30일 신규 칼럼 수",              "30일"),
    ("BRAVO",   "Daily research 중 Claude Code 관련",  "Claude Code"),
    ("CHARLIE", "JY가 작성한 글 중 agent 관련 주제",     "JY"),
    ("DELTA",   "최근 결정된 5/5 파일럿 관련 액션",      "5/5 파일럿"),
    ("ECHO",    "라이언이 4월에 쓴 mirror-loop 칼럼",   "mirror-loop"),
]


async def _one_stream(marker: str, query: str, signature: str, token: str) -> dict:
    """Run one stream end-to-end and return diagnostics."""
    headers = {"Authorization": f"Bearer {token}"}
    t0 = time.time()
    async with httpx.AsyncClient() as client:
        cr = await client.post(
            f"{_platform_base()}/api/v1/conversations",
            json={"title": f"validator-concur-{marker}"}, headers=headers, timeout=10,
        )
        cr.raise_for_status()
        cid = cr.json()["id"]

    res = await _consume_sse(
        f"{_langgraph_base()}/v1/sediment/stream",
        {"conv_id": cid, "query": query}, headers, timeout=60,
    )
    res["marker"] = marker
    res["query"] = query
    res["signature"] = signature
    res["conv_id"] = cid
    res["total_ms"] = int((time.time() - t0) * 1000)
    return res


async def check_concurrent_sse(spec: dict, **_) -> dict:
    """Run 5 SSE streams in parallel; verify isolation + completion."""
    token, _, _ = await _admin_token()
    tasks = [asyncio.create_task(_one_stream(m, q, s, token)) for m, q, s in _QUERIES]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    failures: list[str] = []
    elapsed: list[int] = []
    for r in results:
        if isinstance(r, BaseException):
            failures.append(f"task raised: {type(r).__name__}: {r}")
            continue
        if not r.get("saw_done"):
            failures.append(f"{r.get('marker','?')} did not reach [DONE]")
        if r.get("delta_count", 0) < 1:
            failures.append(f"{r.get('marker','?')} got 0 delta events")
        elapsed.append(r.get("elapsed_ms") or 0)

    # Cross-leakage detection — only fires when the OTHER stream's signature
    # appears in THIS stream's answer. Each signature is hand-picked to be
    # domain-specific to its query (e.g. "mirror-loop", "5/5 파일럿") so we
    # don't false-positive on generic shared vocabulary ("agent", "research").
    for r in results:
        if isinstance(r, BaseException):
            continue
        my_marker = r["marker"]
        my_sig = (r.get("signature") or "").lower()
        body = "".join(r.get("deltas", [])).lower()

        # Sanity: my answer should mention MY signature. If not, the system
        # routed to a totally unrelated doc — surface that as a separate failure.
        if my_sig and my_sig not in body:
            failures.append(f"{my_marker} answer missing own signature '{my_sig}'")

        for other in results:
            if isinstance(other, BaseException) or other["marker"] == my_marker:
                continue
            other_sig = (other.get("signature") or "").lower()
            if other_sig and other_sig in body and other_sig != my_sig:
                failures.append(
                    f"{my_marker} answer contains {other['marker']}'s signature '{other_sig}'"
                )

    p95_ms = sorted(elapsed)[-1] if elapsed else 0  # 5 streams, p95 ≈ max
    # Real LLM answers can take 10-20s under concurrency; ceiling raised from
    # 30s (offline mock) to 60s for the SNT-key test path.
    if p95_ms > 60000:
        failures.append(f"p95 elapsed_ms={p95_ms} > 60000ms sanity ceiling")

    passed = not failures
    return {
        "passed": passed,
        "actual": {
            "streams": len(results),
            "completed": sum(1 for r in results
                              if not isinstance(r, BaseException) and r.get("saw_done")),
            "p95_ms": p95_ms,
            "failures": failures[:5],
        },
        "message": "" if passed else "; ".join(failures[:3]),
    }
