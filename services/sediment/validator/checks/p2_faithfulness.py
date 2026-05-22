"""P2-FAITH-* — citation-faithfulness eval for chat answers.

Pattern adapted from RAGAS (paper) but implemented in-house to avoid the
ragas/datasets/langchain transitive dependency tower. The check:

1. Reads golden_queries.yaml (canonical 40+ KO/EN cases).
2. For each query, calls /v1/sediment/stream (or replays from
   messages table when SEDIMENT_FAITH_REPLAY=1) to obtain
   (answer, citations).
3. Sends a faithfulness rubric prompt to Claude Sonnet with the
   (answer, citations) pair. Score 0..1.
4. Aggregates → mean, p25 — emits json result.

Threshold (per docs/design/15-self-improving-rag §3 Phase 1):
  faithfulness ≥ 0.75      (P2-FAITH-MEAN, blocker)
  faithfulness_p25 ≥ 0.50  (P2-FAITH-P25, major — catches tail regressions)
"""
from __future__ import annotations
import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, quantiles
from typing import Optional

import httpx

from lab_lib.settings import settings


# ============================================================
# Schema
# ============================================================

@dataclass
class FaithfulnessResult:
    query: str
    answer: str
    citation_count: int
    score: float       # 0..1
    reason: str        # judge rationale (short)
    raw_score: int     # 1..5 from judge


# ============================================================
# Judge prompt
# ============================================================

JUDGE_SYSTEM = """You are a strict citation-faithfulness judge. Given a user
question, an AI answer, and the citations the AI was shown, you decide
whether EVERY factual claim in the answer is directly supported by at
least one of the citations.

Score on a 1–5 scale:
  5 = every claim is verbatim or paraphrase of a citation chunk
  4 = every claim is supported, with at most 1 minor inference
  3 = most claims supported, 1 claim is plausible but not in citations
  2 = several claims unsupported / extrapolated
  1 = answer is largely fabrication or hallucinated

Output STRICTLY this JSON, nothing else:
  {"score": <1-5>, "reason": "<one sentence>"}
"""

JUDGE_USER_TPL = """Question: {query}

Citations:
{citations_block}

AI answer:
{answer}

Score the answer's faithfulness to the citations."""


def _format_citations(citations: list[dict], limit_chars: int = 600) -> str:
    """Render citations as a numbered block. Truncate long bodies."""
    if not citations:
        return "(no citations were retrieved)"
    lines = []
    for i, c in enumerate(citations, 1):
        ref = c.get("ref") or c.get("type") or "?"
        content = (c.get("content") or "")[:limit_chars]
        if c.get("type") == "summary":
            # Special-case the vault-summary citation (no content, but
            # `by_type` array gives structured counts).
            by_type = c.get("by_type") or []
            content = "vault counts: " + ", ".join(
                f"{x.get('type')}={x.get('n')}" for x in by_type
            )
        lines.append(f"[{i}] ({ref})\n{content}\n")
    return "\n".join(lines)


# ============================================================
# Judge call (Claude via Anthropic SDK)
# ============================================================

async def _judge_one(query: str, answer: str, citations: list[dict]) -> FaithfulnessResult:
    """Single judge call. No swap-and-average — that's for pairwise
    judges. For pointwise faithfulness, the prompt is symmetric so
    swap-and-average doesn't apply."""
    # Lazy import — don't force anthropic on every validator run.
    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    msg = await client.messages.create(
        model=os.environ.get("FAITH_JUDGE_MODEL", "claude-haiku-4-5-20251001"),
        max_tokens=200,
        system=JUDGE_SYSTEM,
        messages=[{
            "role": "user",
            "content": JUDGE_USER_TPL.format(
                query=query,
                citations_block=_format_citations(citations),
                answer=answer or "(empty answer)",
            ),
        }],
    )
    raw = msg.content[0].text if msg.content else ""
    # Tolerant parse — judges sometimes wrap JSON in markdown
    raw_strip = raw.strip().strip("`").lstrip("json").strip()
    try:
        d = json.loads(raw_strip)
        score_raw = int(d.get("score", 0))
        reason = str(d.get("reason", ""))[:200]
    except Exception:
        score_raw = 0
        reason = f"judge returned unparseable: {raw[:80]}"
    # Normalize 1..5 → 0..1 (with 0 for unparseable)
    score = max(0.0, (score_raw - 1) / 4.0) if score_raw else 0.0
    return FaithfulnessResult(
        query=query, answer=answer, citation_count=len(citations),
        score=score, reason=reason, raw_score=score_raw,
    )


# ============================================================
# Pipeline driver
# ============================================================

def _derive_langgraph_url(platform_url: str) -> str:
    """Local dev: platform=:10101 + langgraph=:10020. Prod: same host
    (nginx fronts both behind one URL). Same swap rule as the CLI in
    services/sediment-cli/src/commands.rs:derive_langgraph_base."""
    return platform_url.replace(":10101", ":10020").replace(":10100", ":10020")


async def _run_chat(
    platform: httpx.AsyncClient, langgraph: httpx.AsyncClient,
    query: str, token: str,
) -> tuple[str, list[dict]]:
    """POST /v1/sediment/stream, accumulate answer + citations."""
    # 1. conv (platform)
    r = await platform.post(
        "/api/v1/conversations",
        json={"title": query[:50]},
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()
    conv_id = r.json()["id"]
    # 2. stream (langgraph)
    answer_parts: list[str] = []
    citations: list[dict] = []
    async with langgraph.stream(
        "POST", "/v1/sediment/stream",
        json={"conv_id": conv_id, "query": query},
        headers={"Authorization": f"Bearer {token}"},
        timeout=120.0,
    ) as resp:
        resp.raise_for_status()
        buf = ""
        cur_event = "message"
        async for chunk in resp.aiter_bytes():
            buf += chunk.decode("utf-8", errors="ignore")
            while "\n\n" in buf:
                frame, buf = buf.split("\n\n", 1)
                cur_event = "message"
                data_lines: list[str] = []
                for line in frame.split("\n"):
                    if line.startswith("event: "):
                        cur_event = line[7:].strip()
                    elif line.startswith("data: "):
                        data_lines.append(line[6:])
                data = "\n".join(data_lines)
                if data == "[DONE]":
                    break
                try:
                    payload = json.loads(data)
                except Exception:
                    continue
                if cur_event == "delta":
                    v = payload.get("v")
                    if isinstance(v, str):
                        answer_parts.append(v)
                elif cur_event == "citation":
                    c = payload.get("v")
                    if isinstance(c, dict):
                        citations.append(c)
    return "".join(answer_parts).strip(), citations


async def run_faithfulness(
    base_url: str,
    token: str,
    queries: list[str],
    *,
    max_concurrent: int = 3,
) -> list[FaithfulnessResult]:
    sem = asyncio.Semaphore(max_concurrent)
    results: list[FaithfulnessResult] = []
    langgraph_url = _derive_langgraph_url(base_url)
    async with httpx.AsyncClient(base_url=base_url, timeout=120) as platform, \
               httpx.AsyncClient(base_url=langgraph_url, timeout=120) as langgraph:
        async def _one(q: str) -> Optional[FaithfulnessResult]:
            async with sem:
                try:
                    answer, citations = await _run_chat(platform, langgraph, q, token)
                except Exception as e:
                    return FaithfulnessResult(
                        query=q, answer="", citation_count=0,
                        score=0.0, reason=f"pipeline error: {e}", raw_score=0,
                    )
                return await _judge_one(q, answer, citations)
        for r in await asyncio.gather(*[_one(q) for q in queries]):
            if r is not None:
                results.append(r)
    return results


# ============================================================
# Rubric check entry points (called by validator harness)
# ============================================================

async def check_faithfulness_mean(spec: dict, **_) -> dict:
    """P2-FAITH-MEAN — mean faithfulness >= threshold."""
    rs = await _collect_results(spec)
    if not rs:
        return {"passed": False, "actual": 0.0, "reason": "no results"}
    m = mean(r.score for r in rs)
    threshold = float(spec.get("threshold", 0.75))
    return {
        "passed": m >= threshold,
        "actual": round(m, 3),
        "threshold": threshold,
        "n": len(rs),
    }


async def check_faithfulness_p25(spec: dict, **_) -> dict:
    """P2-FAITH-P25 — 25th percentile faithfulness >= threshold (tail check)."""
    rs = await _collect_results(spec)
    if len(rs) < 4:
        return {"passed": True, "actual": None, "reason": "n<4"}
    qs = quantiles((r.score for r in rs), n=4)
    p25 = qs[0]
    threshold = float(spec.get("threshold", 0.50))
    return {
        "passed": p25 >= threshold,
        "actual": round(p25, 3),
        "threshold": threshold,
        "n": len(rs),
    }


_CACHED_RESULTS: dict[str, list[FaithfulnessResult]] = {}


async def _collect_results(spec: dict) -> list[FaithfulnessResult]:
    """Cache per-rubric-invocation so MEAN + P25 don't re-run the
    expensive judge calls."""
    key = spec.get("_cache_key") or "default"
    if key in _CACHED_RESULTS:
        return _CACHED_RESULTS[key]

    base_url = spec.get("base_url") or os.environ.get(
        "SEDIMENT_E2E_API_URL", "http://localhost:10101"
    )
    token = spec.get("token") or os.environ.get("SEDIMENT_E2E_JWT")
    if not token:
        # Mint a dev-token locally
        async with httpx.AsyncClient(base_url=base_url, timeout=10) as client:
            r = await client.post("/api/v1/auth/dev-token",
                                   json={"email": spec.get("email", "jay.lee@sonatus.com")})
            r.raise_for_status()
            token = r.json()["token"]

    # Read golden_queries.yaml
    import yaml
    path = Path(spec.get("golden_path") or
                Path(__file__).resolve().parents[1] / "golden_queries.yaml")
    with open(path) as f:
        data = yaml.safe_load(f)
    # Schema: queries:[{id, q, expected_intent, ideal_refs, ...}]. Field is `q`.
    queries = [q["q"] for q in data.get("queries", []) if q.get("q")][: spec.get("limit", 40)]

    results = await run_faithfulness(base_url, token, queries)
    _CACHED_RESULTS[key] = results

    # Dump per-query results next to validator output for debugging
    out_dir = Path(os.environ.get("VALIDATOR_OUTPUT_DIR", "output/validation"))
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "p2-faithfulness-per-query.json", "w") as f:
        json.dump(
            [{"query": r.query[:80], "score": r.score, "raw": r.raw_score,
              "reason": r.reason, "citation_count": r.citation_count}
             for r in results],
            f, indent=2, ensure_ascii=False,
        )
    return results
