"""UT: scheduler.py picks up all sediment#15 cron entries.

Catches the class of bug where a cron.yaml key is added but the
scheduler code doesn't know about it — the job silently never runs.
That's exactly what slipped through on the first pass of Phase 5.
"""
from __future__ import annotations
import asyncio
from pathlib import Path

import pytest
import yaml


SEDIMENT_KEYS = {
    "signal_derivation",
    "judge_daily",
    "hard_negative_mining",
    "chunking_ablation",
    "ab_compare",
    "dspy_bootstrap",
}


def _load_cron():
    p = Path(__file__).resolve().parents[1] / "config/cron.yaml"
    with open(p) as f:
        return yaml.safe_load(f) or {}


# YAML-only — always runs.
def test_cron_yaml_has_all_phase_keys():
    cfg = _load_cron()
    missing = SEDIMENT_KEYS - set(cfg.keys())
    assert not missing, f"cron.yaml missing Phase 2-5 keys: {missing}"


def test_signal_derivation_window_parses():
    """Edge: '30m' / '1h' / '2d' window strings must parse to seconds.
    Catches the bug where YAML 'window: 30m' silently became window_sec=0."""
    cfg = _load_cron()
    sd = cfg.get("signal_derivation") or {}
    w = str(sd.get("window", "30m"))
    unit = w[-1]; val = int(w[:-1])
    secs = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit] * val
    assert secs >= 60, f"signal_derivation window too short: {secs}s ({w})"


def test_scheduler_register_picks_up_phase_keys():
    """Run scheduler._register against cron.yaml and assert every
    Phase 2-5 key gets a job. Skip locally if apscheduler isn't in venv
    (it's in the prod image); CI installs it."""
    pytest.importorskip("apscheduler", reason="apscheduler not in dev venv")
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    import scripts.scheduler as scheduler_mod

    cfg = _load_cron()
    s = AsyncIOScheduler()
    asyncio.run(scheduler_mod._register(s, cfg))

    job_ids = {j.id for j in s.get_jobs()}
    for k in SEDIMENT_KEYS:
        # dspy default: disabled. Skip the assertion for it unless
        # explicitly enabled in cron.yaml.
        default_enabled = k != "dspy_bootstrap"
        if (cfg.get(k) or {}).get("enabled", default_enabled):
            assert k in job_ids, (
                f"cron.yaml has '{k}' enabled but scheduler.py didn't register a job. "
                f"Got: {sorted(job_ids)}"
            )
