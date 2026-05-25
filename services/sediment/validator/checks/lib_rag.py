"""RAG quality checks — golden 40 queries, recall@k, MRR.

Loads validator/golden_queries.yaml, runs each through library /search,
computes recall@k against ideal_refs.

LLM-judge faithfulness/answer_relevancy is NOT included here (cheap CI).
Run separately via tests/eval/test_rag_quality.py with RAGAS for nightly.
"""
from __future__ import annotations
import asyncio
import time
import statistics
from pathlib import Path
import yaml
import httpx

from lab_lib.auth import mint_token
from lab_lib.db import service_session
from lab_lib.settings import settings
from sqlalchemy import text

GOLDEN_PATH = Path(__file__).resolve().parent.parent / "golden_queries.yaml"


async def _make_token() -> str:
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT m.id::text, t.id::text FROM members m JOIN tenants t ON t.id=m.tenant_id
            WHERE t.slug='hypeproof-lab' AND m.email='jayleekr0125@gmail.com'
        """))
        mid, tid = r.first()
    return mint_token(member_id=mid, tenant_id=tid, role="admin", email="jayleekr0125@gmail.com")


async def _run_golden(token: str) -> tuple[list[dict], list[float]]:
    items = yaml.safe_load(GOLDEN_PATH.read_text())["queries"]
    base = f"http://localhost:{settings.sediment_platform_port}/api/v1/library/search"
    out = []
    latencies = []
    async with httpx.AsyncClient() as client:
        for q in items:
            t0 = time.time()
            r = await client.get(base, params={"q": q["q"], "limit": 5},
                                 headers={"Authorization": f"Bearer {token}"}, timeout=20)
            elapsed_ms = int((time.time() - t0) * 1000)
            latencies.append(elapsed_ms)
            if r.status_code != 200:
                out.append({"id": q["id"], "hits": [], "latency_ms": elapsed_ms,
                            "ideal_refs": q.get("ideal_refs", [])})
                continue
            items_resp = r.json().get("items", [])
            refs = [it["ref"] for it in items_resp]
            out.append({"id": q["id"], "hits": refs, "latency_ms": elapsed_ms,
                        "ideal_refs": q.get("ideal_refs", []),
                        "ideal_authors": q.get("ideal_authors", [])})
    return out, latencies


def _recall_at_k(hits: list[str], ideal: list[str], k: int) -> float:
    if not ideal:
        return 1.0  # no expectation → trivially passes
    topk = hits[:k]
    # ideal_refs are path prefixes per golden_queries.yaml contract — use startswith
    found = sum(1 for ideal_pfx in ideal if any(hit.startswith(ideal_pfx) for hit in topk))
    return found / len(ideal)


def _reciprocal_rank(hits: list[str], ideal: list[str]) -> float:
    if not ideal:
        return 0.0
    for i, h in enumerate(hits, start=1):
        if any(h.startswith(p) for p in ideal):
            return 1.0 / i
    return 0.0


async def check_recall_at_k(spec: dict, k: int = 3, min_pct: int = 80, **_) -> dict:
    if not GOLDEN_PATH.exists():
        return {"passed": False, "message": f"missing {GOLDEN_PATH}"}
    token = await _make_token()
    rows, _ = await _run_golden(token)
    if not rows:
        return {"passed": False, "message": "no queries to evaluate"}
    recalls = [_recall_at_k(r["hits"], r["ideal_refs"], k) for r in rows]
    avg = sum(recalls) / len(recalls) * 100
    ok = avg >= min_pct
    failed = [r["id"] for r, rec in zip(rows, recalls) if rec < 0.5]
    return {
        "passed": ok,
        "actual": {"recall_at_k_pct": round(avg, 1), "failed_ids": failed[:10],
                   "total": len(rows)},
        "expected": {f"recall@{k}_pct": f">= {min_pct}"},
        "message": "" if ok else f"recall@{k}={avg:.1f}% < {min_pct}%; failed: {failed[:5]}",
    }


async def check_mrr(spec: dict, min_mrr: float = 0.5, **_) -> dict:
    token = await _make_token()
    rows, _ = await _run_golden(token)
    if not rows:
        return {"passed": False, "message": "no queries"}
    rrs = [_reciprocal_rank(r["hits"], r["ideal_refs"]) for r in rows]
    mrr = sum(rrs) / len(rrs)
    return {
        "passed": mrr >= min_mrr,
        "actual": {"mrr": round(mrr, 3)},
        "expected": {"mrr": f">= {min_mrr}"},
        "message": "" if mrr >= min_mrr else f"MRR={mrr:.3f} < {min_mrr}",
    }


async def check_search_latency(spec: dict, max_p50_ms: int = 1000, **_) -> dict:
    token = await _make_token()
    _, latencies = await _run_golden(token)
    p50 = statistics.median(latencies) if latencies else 0
    p95 = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else max(latencies) if latencies else 0
    ok = p50 <= max_p50_ms
    return {
        "passed": ok,
        "actual": {"p50_ms": int(p50), "p95_ms": int(p95), "n": len(latencies)},
        "expected": {"p50_ms": f"<= {max_p50_ms}"},
        "message": "" if ok else f"p50={p50}ms > {max_p50_ms}",
    }
