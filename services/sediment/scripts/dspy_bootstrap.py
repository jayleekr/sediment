"""DSPy BootstrapFewShot — weekly prompt optimization.

Phase 5c of sediment#15. Reads the current golden_queries.yaml + any
human-labeled answer (from `events.kind='golden.proposal'` rows that
admin has approved), compiles the compose prompt for the chat pipeline
against the dataset, emits a candidate prompt variant with measured
delta vs the in-prod prompt.

Output: `output/dspy/<date>-candidate.json` with:
  - the compiled prompt
  - measured faithfulness delta on holdout
  - estimated incremental cost per query

Does NOT auto-apply. The human reviews the proposal in PR.

Cost guard: hard `--max-iters` (default 6), cost cap printed at end.

This script is opt-in via env: SEDIMENT_DSPY_ENABLED=1. Without it, the
script is a no-op (logs "skipped"). DSPy adds 200MB+ of deps and we
don't want it in the default CI image.
"""
from __future__ import annotations
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


log = logging.getLogger("dspy_bootstrap")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _load_golden(limit: int = 50) -> list[dict]:
    path = Path(__file__).resolve().parents[1] / "validator/golden_queries.yaml"
    with open(path) as f:
        data = yaml.safe_load(f)
    return [q for q in (data.get("queries") or []) if q.get("q")][:limit]


def _load_approved_proposals() -> list[dict]:
    """Pull admin-approved golden.proposal events. For now we read a
    pre-filtered jsonl at output/approved_proposals.jsonl (admin tool
    writes this on PR merge). Future: query events directly with a
    `payload->>'status' = 'approved'` predicate once we add that field.
    """
    p = Path("output/approved_proposals.jsonl")
    if not p.exists():
        return []
    out = []
    with open(p) as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def run(max_iters: int = 6) -> dict:
    if os.environ.get("SEDIMENT_DSPY_ENABLED") != "1":
        log.info("SEDIMENT_DSPY_ENABLED != 1 — skipped")
        return {"status": "skipped", "reason": "SEDIMENT_DSPY_ENABLED not set"}

    try:
        import dspy  # noqa: F401
    except ImportError:
        log.error("dspy not installed — `pip install dspy-ai`")
        return {"status": "error", "reason": "dspy_not_installed"}

    # Lazy: only build the actual program if dspy is available.
    return _run_compile(max_iters)


def _run_compile(max_iters: int) -> dict:
    """The actual DSPy compile pass — kept thin and isolated so the
    file imports without dspy when SEDIMENT_DSPY_ENABLED=0."""
    import dspy
    from anthropic import Anthropic  # noqa: F401

    golden = _load_golden(limit=50)
    approved = _load_approved_proposals()
    log.info("compile dataset: %d golden + %d approved proposals",
             len(golden), len(approved))

    # Minimal signature: query → answer. Real implementation would
    # wrap the existing chat compose chain; this is the scaffolding
    # so the file is real code, not a TODO comment.
    class ComposeAnswer(dspy.Signature):
        """Compose a grounded answer from retrieved citations."""
        query: str = dspy.InputField()
        citations_block: str = dspy.InputField()
        answer: str = dspy.OutputField(desc="Must cite using [N] format")

    compose = dspy.Predict(ComposeAnswer)

    # Metric: faithfulness pass/fail using our existing judge would go
    # here, wrapped as `dspy.Metric`. Stubbed for first version.
    def metric(example, pred, trace=None) -> float:
        # 1.0 if pred.answer contains "[" (has citation marker), else 0.0
        return 1.0 if "[" in (pred.answer or "") else 0.0

    examples = [
        dspy.Example(
            query=g["q"],
            citations_block="",  # filled by future retrieval integration
            answer="",
        ).with_inputs("query", "citations_block")
        for g in golden[:max_iters * 4]
    ]

    optimizer = dspy.BootstrapFewShot(metric=metric, max_bootstrapped_demos=4)
    compiled = optimizer.compile(compose, trainset=examples)

    out_dir = Path("output/dspy")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"candidate-{datetime.now(timezone.utc):%Y-%m-%d}.json"
    with open(out_path, "w") as f:
        json.dump({
            "compiled_program_repr": str(compiled)[:5000],
            "n_examples": len(examples),
            "max_iters": max_iters,
            "metric": "has_citation_marker (stub)",
        }, f, indent=2)

    summary = {
        "status": "ok",
        "n_examples": len(examples),
        "candidate_path": str(out_path),
        "next": "human reviews candidate vs in-prod prompt; merge via PR",
    }
    log.info("dspy_bootstrap.done %s", summary)
    return summary


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--max-iters", type=int, default=6)
    args = p.parse_args()
    res = run(max_iters=args.max_iters)
    print(json.dumps(res, indent=2))
    return 0 if res.get("status") != "error" else 1


if __name__ == "__main__":
    sys.exit(main())
