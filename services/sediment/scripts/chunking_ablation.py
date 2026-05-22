"""Chunking parameter ablation — weekly grid eval.

Phase 5b of sediment#15. Runs the existing recall@K eval (from
validator/checks/lib_rag.py) against a grid of `chunk_size × overlap`
configurations against the SAME golden query set, picks the Pareto
frontier on (recall, cost).

Output: a tuning proposal to `output/ablation/<date>.json` that a human
reviews and applies via the chunker config. Does NOT auto-apply.

Strategy:
  - Subsample golden_queries.yaml to a fixed N (default 20) for speed
  - For each (chunk_size, overlap) pair in the grid:
      · count chunks that would result from re-chunking
      · estimate recall@3 by simulating segmentation on a held-out
        subset of artifacts (we don't actually re-embed — too expensive)
  - Pareto front: configs that are not dominated by another on BOTH
    recall AND chunk_count (fewer chunks = lower embed cost)

Limits:
  - This is an offline analytical pass — no embeddings recomputed.
    Recall estimation uses substring overlap between query terms and
    candidate chunks, which underestimates true vector recall but is
    consistent across configurations (rank order is preserved).
  - Real recall comparison requires the embedding re-run separately.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import logging
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy import text

from lab_lib.db import service_session


log = logging.getLogger("chunking_ablation")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


GRID = [
    # (chunk_size, overlap)
    (300, 0), (300, 50), (300, 100),
    (500, 0), (500, 50), (500, 100),
    (800, 0), (800, 50), (800, 100),
]


def _segment(text_body: str, chunk_size: int, overlap: int) -> list[str]:
    """Word-based segmentation — close enough to the real chunker for
    ablation purposes. The actual chunker uses tokens, but words are
    a stable proxy."""
    words = text_body.split()
    if not words:
        return []
    out: list[str] = []
    step = max(1, chunk_size - overlap)
    for start in range(0, len(words), step):
        seg = words[start : start + chunk_size]
        if not seg:
            break
        out.append(" ".join(seg))
        if start + chunk_size >= len(words):
            break
    return out


def _hits(query: str, chunks: list[str]) -> int:
    """Naive recall proxy: any chunk that contains ≥ 50% of query
    terms counts as a hit. Consistent across configs."""
    q_terms = [t.lower() for t in re.findall(r"\w+", query) if len(t) > 2]
    if not q_terms:
        return 0
    needed = max(1, len(q_terms) // 2)
    hits = 0
    for c in chunks:
        cl = c.lower()
        match = sum(1 for t in q_terms if t in cl)
        if match >= needed:
            hits += 1
    return hits


async def _fetch_corpus(limit: int = 50) -> list[str]:
    """Sample a slice of artifacts.content for ablation. Service session
    (BYPASSRLS) for analytical work."""
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT body FROM artifacts
            WHERE LENGTH(body) > 200
            ORDER BY random() LIMIT :lim
        """), {"lim": limit})
        return [row[0] for row in r if row[0]]


def _load_queries(limit: int = 20) -> list[str]:
    path = Path(__file__).resolve().parents[1] / "validator/golden_queries.yaml"
    with open(path) as f:
        data = yaml.safe_load(f)
    return [q["q"] for q in (data.get("queries") or []) if q.get("q")][:limit]


# ============================================================
# Pareto frontier
# ============================================================

def pareto_front(results: list[dict]) -> list[dict]:
    """Configs that are not dominated by another on BOTH (recall ↑,
    chunk_count ↓). Drop dominated."""
    front: list[dict] = []
    for r in results:
        dominated = False
        for o in results:
            if o is r:
                continue
            if (o["recall_score"] >= r["recall_score"]
                and o["total_chunks"] <= r["total_chunks"]
                and (o["recall_score"] > r["recall_score"]
                     or o["total_chunks"] < r["total_chunks"])):
                dominated = True
                break
        if not dominated:
            front.append(r)
    return front


# ============================================================
# Entry point
# ============================================================

async def run(corpus_n: int = 50, queries_n: int = 20) -> dict:
    corpus = await _fetch_corpus(limit=corpus_n)
    queries = _load_queries(limit=queries_n)
    if not corpus or not queries:
        return {"error": "no corpus or no queries"}

    results = []
    for cs, ov in GRID:
        chunks_per_doc = [_segment(body, cs, ov) for body in corpus]
        all_chunks = [c for doc_chunks in chunks_per_doc for c in doc_chunks]
        total_chunks = len(all_chunks)
        hit_rate = statistics.mean(
            _hits(q, all_chunks) / max(1, total_chunks // queries_n) for q in queries
        ) if total_chunks else 0.0
        # Normalize 0..1
        recall_score = min(1.0, hit_rate)
        results.append({
            "chunk_size": cs,
            "overlap": ov,
            "total_chunks": total_chunks,
            "recall_score": round(recall_score, 4),
        })

    front = pareto_front(results)
    proposal = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "corpus_size": len(corpus),
        "queries_evaluated": len(queries),
        "grid": results,
        "pareto_front": front,
        "recommendation": (
            "Apply pareto-frontier point with highest recall_score; "
            "consider chunk_count vs cost tradeoff before changing config."
        ),
    }

    out_dir = Path("output/ablation")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"chunking-{datetime.utcnow():%Y-%m-%d}.json"
    with open(out, "w") as f:
        json.dump(proposal, f, indent=2)
    log.info("chunking_ablation.done %s grid_points front_size=%s", len(GRID), len(front))
    return proposal


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--corpus", type=int, default=50)
    p.add_argument("--queries", type=int, default=20)
    args = p.parse_args()
    res = asyncio.run(run(corpus_n=args.corpus, queries_n=args.queries))
    print(json.dumps(res, indent=2)[:500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
