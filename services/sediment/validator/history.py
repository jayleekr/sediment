"""Durable per-run metric history — one JSON line per run.

Appends to output/validation/history/<metric>.jsonl a record:
  {"ts", "phase", "metric", "value", "n", "git_sha"}
Best-effort: never raises into a check. History is gitignored (VM/CI-local);
nightly workflows upload it as an artifact.
"""
from __future__ import annotations
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def _git_sha() -> str:
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha[:12]
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return out or "unknown"
    except Exception:
        return "unknown"


def append_history(metric: str, value, *, phase: str, n: int) -> None:
    """Append one datapoint. Swallows all errors."""
    try:
        out_dir = Path(os.environ.get("VALIDATOR_OUTPUT_DIR", "output/validation")) / "history"
        out_dir.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "phase": phase,
            "metric": metric,
            "value": value,
            "n": n,
            "git_sha": _git_sha(),
        }
        with open(out_dir / f"{metric}.jsonl", "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
