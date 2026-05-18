"""Validator runner — load rubric, execute checks for a phase, write reports."""
from __future__ import annotations
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
import yaml

from lab_lib.logging import configure_logging, get_logger
from .dispatch import dispatch
from .types import PhaseReport
from .report import write_reports, print_console

configure_logging()
log = get_logger("validator.runner")

VALIDATOR_DIR = Path(__file__).resolve().parent
RUBRIC_PATH = VALIDATOR_DIR / "rubric.yaml"


def load_rubric(path: Path = RUBRIC_PATH) -> dict:
    with path.open() as f:
        return yaml.safe_load(f)


def find_phase(rubric: dict, phase_id: str) -> dict:
    for p in rubric["phases"]:
        if p["id"].upper() == phase_id.upper():
            return p
    raise ValueError(f"phase not found: {phase_id}. available: {[p['id'] for p in rubric['phases']]}")


async def run_phase(phase_id: str, iteration: int = 1, only_layers: list[str] | None = None) -> PhaseReport:
    rubric = load_rubric()
    phase = find_phase(rubric, phase_id)

    report = PhaseReport(
        phase_id=phase["id"],
        phase_name=phase["name"],
        iteration=iteration,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    t0 = time.time()
    checks = phase["checks"]
    if only_layers:
        only_layers_upper = {L.upper() for L in only_layers}
        checks = [c for c in checks if c["layer"].upper() in only_layers_upper]

    log.info("phase.start", phase=phase["id"], n_checks=len(checks), iter=iteration)

    for spec in checks:
        log.info("check.run", id=spec["id"])
        try:
            result = await dispatch(spec)
        except Exception as e:
            log.exception("check.dispatch_failed", id=spec["id"])
            from .types import CheckResult
            result = CheckResult(
                id=spec["id"], title=spec["title"], layer=spec["layer"],
                severity=spec["severity"], passed=False, message=f"dispatcher error: {e}",
            )
        report.results.append(result)
        log.info("check.done", id=spec["id"], passed=result.passed,
                 layer=result.layer, ms=result.duration_ms)

    report.elapsed_s = round(time.time() - t0, 2)
    return report


async def main_async(phase_id: str, iteration: int = 1, output_dir: Path | None = None,
                     only_layers: list[str] | None = None) -> int:
    report = await run_phase(phase_id, iteration=iteration, only_layers=only_layers)

    if output_dir is None:
        output_dir = Path(__file__).resolve().parents[2].parent / "output" / "validation"
    output_dir.mkdir(parents=True, exist_ok=True)

    ts = report.timestamp.replace(":", "-").split(".")[0]
    base = output_dir / f"{report.phase_id}-iter{iteration:02d}-{ts}"
    write_reports(report, base)

    # latest symlinks (per phase)
    latest = output_dir / f"{report.phase_id}-latest.json"
    latest_md = output_dir / f"{report.phase_id}-latest.md"
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        if latest_md.is_symlink() or latest_md.exists():
            latest_md.unlink()
        latest.symlink_to(base.with_suffix(".json").name)
        latest_md.symlink_to(base.with_suffix(".md").name)
    except OSError:
        pass

    print_console(report)

    if not report.all_blockers_passed:
        return 1
    if report.score_pct < 90:
        return 2
    return 0


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="AI Curator validator")
    parser.add_argument("--phase", required=True, help="P0|P1|P2|P3")
    parser.add_argument("--iteration", type=int, default=1)
    parser.add_argument("--only-layers", nargs="+", help="L1 L3 ...")
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()

    rc = asyncio.run(main_async(args.phase, args.iteration, args.output_dir,
                                 only_layers=args.only_layers))
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
