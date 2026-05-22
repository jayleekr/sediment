"""UT for the faithfulness check (no live LLM call).

We mock `_judge_one` so this runs in CI without an Anthropic API key.
The shape contract is what matters here: mean/p25 computed from a list
of FaithfulnessResult dataclasses, threshold gating, file dump.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from validator.checks.p2_faithfulness import (
    FaithfulnessResult,
    check_faithfulness_mean,
    check_faithfulness_p25,
    _CACHED_RESULTS,
)


def _fake_results(scores: list[float]) -> list[FaithfulnessResult]:
    return [
        FaithfulnessResult(
            query=f"q{i}", answer=f"a{i}", citation_count=2,
            score=s, reason=f"mocked {s}", raw_score=int(s * 4) + 1,
        )
        for i, s in enumerate(scores)
    ]


@pytest.fixture(autouse=True)
def _reset_cache():
    _CACHED_RESULTS.clear()
    yield
    _CACHED_RESULTS.clear()


async def test_mean_passes_at_threshold():
    _CACHED_RESULTS["fixture"] = _fake_results([0.75, 1.0, 0.75, 1.0])
    out = await check_faithfulness_mean({"_cache_key": "fixture", "threshold": 0.75})
    assert out["passed"] is True
    assert out["actual"] == 0.875
    assert out["n"] == 4


async def test_mean_fails_below_threshold():
    _CACHED_RESULTS["fixture"] = _fake_results([0.5, 0.5, 0.75])
    out = await check_faithfulness_mean({"_cache_key": "fixture", "threshold": 0.75})
    assert out["passed"] is False
    # 0.5833... rounded to 0.583
    assert out["actual"] == 0.583


async def test_p25_catches_tail_regression():
    # Median good (0.875), but bottom quartile is 0.25 — must fail P25 ≥ 0.5
    _CACHED_RESULTS["fixture"] = _fake_results([0.25, 0.5, 1.0, 1.0])
    mean_out = await check_faithfulness_mean({"_cache_key": "fixture", "threshold": 0.5})
    p25_out  = await check_faithfulness_p25 ({"_cache_key": "fixture", "threshold": 0.5})
    # Mean passes the lower bar, but P25 should still flag the tail
    assert mean_out["passed"] is True
    assert p25_out["passed"] is False, f"p25 was {p25_out['actual']}"


async def test_p25_skipped_for_tiny_n():
    _CACHED_RESULTS["fixture"] = _fake_results([0.0, 0.0, 0.0])
    out = await check_faithfulness_p25({"_cache_key": "fixture"})
    assert out["passed"] is True  # not enough data → no fail


async def test_empty_results_fail():
    _CACHED_RESULTS["fixture"] = []
    out = await check_faithfulness_mean({"_cache_key": "fixture"})
    assert out["passed"] is False
    assert "no results" in out.get("reason", "")
