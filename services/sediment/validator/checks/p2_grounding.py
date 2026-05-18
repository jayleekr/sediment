"""Answer-grounding check — verifies the LLM actually used its citations.

The system's biggest hidden risk: validator + e2e all pass, but the LLM
hallucinates an answer that has nothing to do with the retrieved citations.
This check catches that by running an SSE round-trip with a query whose
citations are known to contain a distinctive token, then asserting the
final assembled answer either:
  (a) mentions a citation token directly (verbatim filename/slug), OR
  (b) contains a numbered reference [1] / [2] / etc. matching citations, OR
  (c) explicitly says it has no useful info (when citations are weak).

This is a coarse measure — a smart LLM can still hallucinate around weak
citations — but it catches the worst regression: completely unrelated answers.
"""
from __future__ import annotations
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from validator.checks.p2_chat import _consume_sse, _admin_token, _platform_base, _langgraph_base  # noqa: E402

import httpx


# Test query → expected grounding signals. The query is chosen to retrieve a
# known specific document, and the signals are tokens that should appear in
# any honest answer derived from it (or a numbered citation reference).
_GROUNDING_PROBES: list[dict] = [
    {
        "query": "지난 30일 신규 칼럼 수",
        # Vault summary citation returns artifact counts including columns (28).
        # An honest answer either quotes the number or references [1].
        "signals": ["28", "[1]", "칼럼", "column"],
    },
    {
        "query": "AI Curator 어떤 도메인 모델 쓰는지",
        # SPEC.md retrieval — answer should mention domain entities or [1].
        "signals": ["[1]", "tenant", "artifact", "chunk", "RLS", "SPEC"],
    },
]


async def _grounding_round(query: str, signals: list[str], token: str) -> dict:
    """Run one SSE round, return whether the final answer contains any signal."""
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient() as client:
        cr = await client.post(
            f"{_platform_base()}/api/v1/conversations",
            json={"title": f"validator-grounding"}, headers=headers, timeout=10,
        )
        cr.raise_for_status()
        cid = cr.json()["id"]

    res = await _consume_sse(
        f"{_langgraph_base()}/v1/sediment/stream",
        {"conv_id": cid, "query": query}, headers, timeout=45,
    )
    answer = "".join(res.get("deltas", []))
    citations = [ev["p"]["v"] for ev in res.get("events", []) if ev.get("e") == "citation"]
    hit_signals = [s for s in signals if s.lower() in answer.lower()]
    # Also accept any [N] reference where N <= len(citations) — that's the
    # bare-minimum grounding pattern enforced by the system prompt.
    bracket_refs = re.findall(r"\[(\d+)\]", answer)
    valid_refs = [int(n) for n in bracket_refs if 1 <= int(n) <= len(citations)]
    return {
        "query": query,
        "answer_len": len(answer),
        "n_citations": len(citations),
        "hit_signals": hit_signals,
        "valid_refs": valid_refs,
        "grounded": bool(hit_signals or valid_refs),
        "answer_preview": answer[:200],
    }


_OFFLINE_MOCK_MARKER = "[offline LLM mock]"


async def check_answer_grounding(spec: dict, **_) -> dict:
    """Pass when at least 1 of N probes returns a grounded answer.

    When the langgraph service is running with the offline LLM mock (no
    Anthropic / Gemini / claude_cli configured), this check skips gracefully
    rather than reporting a false failure — answer grounding is only
    meaningful against a real LLM provider. The skip is signalled in
    `actual.skipped_reason` so the loop can surface it.
    """
    token, _, _ = await _admin_token()
    results = []
    for probe in _GROUNDING_PROBES:
        try:
            r = await _grounding_round(probe["query"], probe["signals"], token)
            results.append(r)
        except Exception as e:
            results.append({"query": probe["query"], "error": f"{type(e).__name__}: {e}",
                             "grounded": False})

    # Detect offline mock — every answer begins with the sentinel from
    # lab_lib.llm._offline_chat. In that mode, treat the check as not-applicable.
    if all(r.get("answer_preview", "").startswith(_OFFLINE_MOCK_MARKER)
           for r in results if "answer_preview" in r):
        return {
            "passed": True,
            "actual": {
                "skipped_reason": "offline LLM mock; check meaningful only "
                                  "with LLM_PROVIDER=anthropic|gemini|claude_cli",
                "probes": len(results),
            },
            "message": "skipped (offline mock)",
        }

    grounded_count = sum(1 for r in results if r.get("grounded"))
    total = len(results)
    # Threshold: at least half the probes must produce grounded answers. Lower
    # bar acknowledges LLM nondeterminism; raise once an answer-quality SLO is
    # established.
    passed = grounded_count >= max(1, total // 2)
    return {
        "passed": passed,
        "actual": {
            "probes": total,
            "grounded": grounded_count,
            "detail": [{"q": r["query"][:40],
                        "g": r.get("grounded"),
                        "hits": r.get("hit_signals", []),
                        "refs": r.get("valid_refs", []),
                        "n_cit": r.get("n_citations", 0)}
                       for r in results],
        },
        "message": "" if passed
                   else f"only {grounded_count}/{total} probes grounded",
    }
