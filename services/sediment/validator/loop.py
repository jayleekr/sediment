"""Self-improving validator loop (max 50 iterations).

Algorithm:
  for i in 1..N:
    1. run validator → report
    2. if converged (blockers + score >= target): exit 0
    3. apply auto-fix recipes
    4. if no progress for 5 iter: exit 2 (stalled)
  exit 1 if max iter reached without convergence

Outputs:
  output/validation/loop-<phase>-<ts>/iter-NN/{report.json, report.md, work-order.json}
  output/validation/loop-<phase>-<ts>/history.csv
  output/validation/loop-<phase>-<ts>/convergence.md
"""
from __future__ import annotations
import asyncio
import csv
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from lab_lib.logging import configure_logging, get_logger
from .runner import run_phase
from .report import write_reports
from .fixer import fix_failures
from .types import PhaseReport

configure_logging()
log = get_logger("validator.loop")

REPO_ROOT = Path(__file__).resolve().parents[5]


async def loop(phase_id: str, max_iter: int = 50, target_score_pct: float = 95,
               stall_window: int = 5, stall_min_delta: float = 2.0,
               cost_budget_usd: float = 50.0) -> int:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_dir = Path(__file__).resolve().parents[2].parent / "output" / "validation" / f"loop-{phase_id}-{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    history_path = out_dir / "history.csv"
    history_fields = ["iter", "timestamp", "phase", "score", "max", "pct",
                       "blockers_passed", "blockers_total", "passed",
                       "fixer_auto_fixed", "fixer_work_orders", "elapsed_s", "cumulative_cost_usd"]
    with history_path.open("w", newline="") as f:
        csv.DictWriter(f, fieldnames=history_fields).writeheader()

    pct_history: list[float] = []
    cumulative_cost = 0.0

    for i in range(1, max_iter + 1):
        log.info("loop.iter.start", iter=i, phase=phase_id)
        os.environ["VALIDATOR_ITER"] = str(i)

        iter_dir = out_dir / f"iter-{i:02d}"
        iter_dir.mkdir(parents=True, exist_ok=True)

        report = await run_phase(phase_id, iteration=i)
        cumulative_cost += report.cost_usd
        write_reports(report, iter_dir / "report")

        # convergence check
        converged = report.all_blockers_passed and report.score_pct >= target_score_pct
        pct_history.append(report.score_pct)

        # apply fixer (only if not converged)
        fix_summary: dict = {"auto_fixed": 0, "work_orders_written": 0}
        if not converged:
            fix_summary = await fix_failures(report, REPO_ROOT, iter_dir)

        # history row
        with history_path.open("a", newline="") as f:
            csv.DictWriter(f, fieldnames=history_fields).writerow({
                "iter": i,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "phase": phase_id,
                "score": report.score,
                "max": report.max_score,
                "pct": round(report.score_pct, 1),
                "blockers_passed": report.blockers_passed,
                "blockers_total": report.blockers_total,
                "passed": converged,
                "fixer_auto_fixed": fix_summary.get("auto_fixed", 0),
                "fixer_work_orders": fix_summary.get("work_orders_written", 0),
                "elapsed_s": report.elapsed_s,
                "cumulative_cost_usd": round(cumulative_cost, 2),
            })

        log.info("loop.iter.done", iter=i, pct=report.score_pct,
                 blockers=f"{report.blockers_passed}/{report.blockers_total}",
                 fixer_applied=fix_summary.get("auto_fixed", 0),
                 wo=fix_summary.get("work_orders_written", 0),
                 cumulative_cost=round(cumulative_cost, 2))

        # CONVERGED ─────────────────────────────────────────
        if converged:
            _write_convergence_md(out_dir, phase_id, i, pct_history,
                                   report, status="converged",
                                   cumulative_cost=cumulative_cost)
            return 0

        # COST BUDGET ───────────────────────────────────────
        if cumulative_cost >= cost_budget_usd:
            _write_convergence_md(out_dir, phase_id, i, pct_history,
                                   report, status="cost_exhausted",
                                   cumulative_cost=cumulative_cost)
            return 3

        # STALL DETECTION ───────────────────────────────────
        if len(pct_history) >= stall_window:
            window = pct_history[-stall_window:]
            delta = max(window) - min(window)
            if delta < stall_min_delta and fix_summary.get("auto_fixed", 0) == 0:
                _write_convergence_md(out_dir, phase_id, i, pct_history,
                                       report, status="stalled",
                                       cumulative_cost=cumulative_cost)
                return 2

    _write_convergence_md(out_dir, phase_id, max_iter, pct_history,
                           report, status="max_iter_reached",
                           cumulative_cost=cumulative_cost)
    return 1


def _write_convergence_md(out_dir: Path, phase_id: str, iters: int, pcts: list[float],
                          last_report: PhaseReport, status: str,
                          cumulative_cost: float) -> None:
    """Convergence report — what happened across the loop."""
    lines = [
        f"# Loop Convergence — {phase_id}",
        "",
        f"- Status: **{status}**",
        f"- Iterations: {iters}",
        f"- Final score: **{last_report.score_pct:.1f}%**",
        f"- Final blockers: {last_report.blockers_passed}/{last_report.blockers_total}",
        f"- Cumulative cost: ${cumulative_cost:.2f}",
        "",
        "## Score curve",
        "",
        "| iter | pct |",
        "|---|---|",
    ]
    for i, p in enumerate(pcts, start=1):
        bar = "█" * int(p / 5)
        lines.append(f"| {i} | {p:.1f}% {bar} |")

    if status == "stalled":
        lines.extend([
            "",
            "## ⚠ STALLED — human intervention required",
            "",
            f"5 iterations changed score by < 2pp and auto-fixer had no recipes.",
            f"Review `iter-{iters:02d}/work-order.json` and apply manual fixes.",
        ])
    elif status == "converged":
        lines.extend(["", "## ✅ Phase passed — proceed to next phase."])
    elif status == "max_iter_reached":
        lines.extend(["", "## ❌ Did not converge in 50 iterations.",
                       "", f"Review `iter-{iters:02d}/work-order.json` and last report."])
    elif status == "cost_exhausted":
        lines.extend(["", f"## 💰 Cost budget exhausted (${cumulative_cost:.2f})",
                       "", "Increase budget via VALIDATOR_COST_BUDGET_USD or split runs."])

    (out_dir / "convergence.md").write_text("\n".join(lines))


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Self-improving validator loop")
    parser.add_argument("--phase", required=True)
    parser.add_argument("--max-iter", type=int, default=50)
    parser.add_argument("--target-pct", type=float, default=95.0)
    parser.add_argument("--stall-window", type=int, default=5)
    parser.add_argument("--stall-delta", type=float, default=2.0)
    parser.add_argument("--cost-budget", type=float,
                        default=float(os.environ.get("VALIDATOR_COST_BUDGET_USD", "50")))
    args = parser.parse_args()

    rc = asyncio.run(loop(args.phase, args.max_iter, args.target_pct,
                          args.stall_window, args.stall_delta, args.cost_budget))
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
