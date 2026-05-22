"""Phase 5 scripts — pure-function tests where possible, IT for DB-touching."""
from __future__ import annotations
import json
import os
import tempfile
from pathlib import Path

import pytest

from scripts.hard_negative_mining import extract_negatives, append_jsonl
from scripts.chunking_ablation import _segment, _hits, pareto_front
from scripts.ab_compare import recommend


pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_DB") == "1", reason="DB not available"
)


# ============================================================
# Hard negative extraction
# ============================================================

def test_extract_negatives_drops_summary_citations():
    row = {
        "msg_id": "m1",
        "query": "x",
        "answer": "y",
        "citations": [
            {"type": "summary", "by_type": [{"type": "note", "n": 5}]},  # skip
            {"ref": "real.md", "content": "real chunk text", "type": "note"},
        ],
        "judge_score": 0.2,
        "thumbs_down": 0,
        "grounding_status": "passed",
    }
    negs = extract_negatives(row)
    assert len(negs) == 1
    assert negs[0]["negative_ref"] == "real.md"
    assert negs[0]["reason"] == "judge_low"


def test_append_jsonl_idempotent():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "neg.jsonl"
        recs = [{"msg_id": "m1", "negative_ref": "a.md", "negative_chunk": "x", "query": "q"}]
        n1 = append_jsonl(recs, p)
        n2 = append_jsonl(recs, p)  # same record again
        assert n1 == 1
        assert n2 == 0
        # File has only one line
        assert sum(1 for _ in open(p)) == 1


# ============================================================
# Chunking segmentation + hits
# ============================================================

def test_segment_emits_overlap():
    text = " ".join(f"w{i}" for i in range(20))  # 20 words
    chunks = _segment(text, chunk_size=10, overlap=2)
    # step = 10 - 2 = 8 → starts: 0, 8, 16 → 3 chunks
    assert len(chunks) == 3
    # First chunk: w0..w9
    assert chunks[0].startswith("w0 w1")


def test_segment_empty_returns_empty():
    assert _segment("", 100, 0) == []


def test_hits_counts_chunks_with_half_query_terms():
    chunks = [
        "mirror loop philosophy doing learning",  # all terms — hit
        "weather in seoul",                       # no terms — miss
        "doing learning is good",                 # 2 of 4 terms — hit (50%)
    ]
    n = _hits("mirror loop doing learning", chunks)
    assert n == 2


def test_pareto_front_drops_dominated():
    # Two non-dominated points (a true tradeoff) + two dominated.
    # - 300/0: high recall (0.8) but many chunks (200) — non-dominated
    # - 800/0: lower recall (0.6) but few chunks (60) — non-dominated
    # - 500/0: middle recall (0.7), middle chunks (100) — dominated only
    #          if another has BOTH ≥ recall AND ≤ chunks. 300/0 has
    #          better recall but more chunks; 800/0 has fewer chunks but
    #          worse recall. So 500/0 IS on the front too (truly
    #          intermediate).
    # - 800/100: lower recall (0.5), more chunks (90) than 800/0 →
    #            dominated.
    results = [
        {"chunk_size": 300, "overlap": 0,   "recall_score": 0.8, "total_chunks": 200},
        {"chunk_size": 500, "overlap": 0,   "recall_score": 0.7, "total_chunks": 100},
        {"chunk_size": 800, "overlap": 0,   "recall_score": 0.6, "total_chunks": 60 },
        {"chunk_size": 800, "overlap": 100, "recall_score": 0.5, "total_chunks": 90 },
    ]
    front = pareto_front(results)
    sizes = sorted([(r["chunk_size"], r["overlap"]) for r in front])
    assert (300, 0) in sizes
    assert (500, 0) in sizes
    assert (800, 0) in sizes
    assert (800, 100) not in sizes  # dominated by 800/0


# ============================================================
# A/B recommend
# ============================================================

def test_recommend_promotes_b_when_better():
    cohorts = [
        {"task_tag": "variant:A", "n": 50, "faith_mean": 0.70, "thumbs_up_rate": 0.40},
        {"task_tag": "variant:B", "n": 50, "faith_mean": 0.80, "thumbs_up_rate": 0.42},
    ]
    rec = recommend(cohorts)
    assert "promote B" in rec


def test_recommend_keeps_a_when_b_regresses():
    cohorts = [
        {"task_tag": "variant:A", "n": 50, "faith_mean": 0.80, "thumbs_up_rate": 0.50},
        {"task_tag": "variant:B", "n": 50, "faith_mean": 0.70, "thumbs_up_rate": 0.45},
    ]
    rec = recommend(cohorts)
    assert "keep A" in rec


def test_recommend_inconclusive_below_sample_size():
    cohorts = [
        {"task_tag": "variant:A", "n": 10, "faith_mean": 0.8, "thumbs_up_rate": 0.5},
        {"task_tag": "variant:B", "n": 10, "faith_mean": 0.9, "thumbs_up_rate": 0.5},
    ]
    rec = recommend(cohorts)
    assert "inconclusive" in rec
