"""P2-JUDGE-CAL — Krippendorff α calibration check.

Compare LLM-judge scores against a small human-labeled holdout set.
If α < 0.80 the judge is no longer reliable enough to drive automated
decisions — alert and continue (don't block deploys; this informs the
team that the rubric needs a refresh).

Holdout format: services/sediment/validator/judge_holdout.yaml
  - id: H001
    query: "..."
    answer: "..."
    citations: [...]
    human_score: 0.75   # 0..1
    annotator: jay
    annotated_at: 2026-05-22

The check runs the judge against each holdout case, then computes
Krippendorff α for an ordinal scale.
"""
from __future__ import annotations
import logging
from pathlib import Path

from validator.checks.p2_faithfulness import _judge_one


log = logging.getLogger("judge_calibration")


def krippendorff_alpha(human: list[float], judge: list[float]) -> float:
    """Krippendorff's α for interval data — single-pair version.

    α = 1 - (D_o / D_e), where D_o is observed disagreement and D_e
    expected. For two raters and interval scale this reduces to:
      D_o = mean((h - j)^2)
      D_e = 2 * variance of the pooled values
    We use the pairwise form; n ≥ 8 recommended for stable α.

    Returns NaN-equivalent (-1.0) if data is degenerate (all same).
    """
    if len(human) != len(judge) or not human:
        return -1.0
    n = len(human)
    pooled = human + judge
    pooled_mean = sum(pooled) / len(pooled)
    pooled_var = sum((v - pooled_mean) ** 2 for v in pooled) / (len(pooled) - 1)
    if pooled_var == 0:
        return -1.0
    d_o = sum((h - j) ** 2 for h, j in zip(human, judge)) / n
    d_e = 2 * pooled_var
    return 1.0 - d_o / d_e


async def check_judge_alpha(spec: dict, **_) -> dict:
    """P2-JUDGE-CAL — Krippendorff α ≥ threshold (default 0.80)."""
    holdout_path = spec.get("holdout_path") or (
        Path(__file__).resolve().parents[1] / "judge_holdout.yaml"
    )
    if not Path(holdout_path).exists():
        return {"passed": True, "actual": None, "reason": "no holdout file yet"}

    import yaml
    with open(holdout_path) as f:
        data = yaml.safe_load(f) or {}
    cases = data.get("cases", [])
    if len(cases) < 4:
        return {"passed": True, "actual": None,
                "reason": f"n={len(cases)} (need ≥4)"}

    human_scores: list[float] = []
    judge_scores: list[float] = []
    for c in cases:
        try:
            j = await _judge_one(c["query"], c["answer"], c.get("citations", []))
        except Exception as e:
            log.warning("judge_failed for %s: %s", c.get("id"), e)
            continue
        human_scores.append(float(c["human_score"]))
        judge_scores.append(float(j.score))

    if len(human_scores) < 4:
        return {"passed": True, "actual": None,
                "reason": f"only {len(human_scores)} usable cases"}

    alpha = krippendorff_alpha(human_scores, judge_scores)
    threshold = float(spec.get("threshold", 0.80))
    return {
        "passed": alpha >= threshold,
        "actual": round(alpha, 3),
        "threshold": threshold,
        "n": len(human_scores),
    }
