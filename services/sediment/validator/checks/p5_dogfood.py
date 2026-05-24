"""Phase 5.5 dogfood gate aggregator.

Implements the 10 measurable criteria from PHASE_5_5_DOGFOOD_GATE.md. 9 are
fully auto-measurable from DB + filesystem; criterion #9 (NPS) reads a manual
input file or skips if absent.

Usage (one-shot, daily cron-friendly):
    cd services/sediment
    .venv/bin/python -m validator.checks.p5_dogfood

Output: prints a JSON snapshot to stdout + writes
products/sediment/output/dogfood/<YYYY-MM-DD>.json.

This module deliberately avoids the rubric.yaml plumbing — it's a standalone
aggregator that the Phase 5.5 daily cron invokes. When/if Phase 6 wants
dogfood criteria as part of P5 rubric, wire individual functions into
rubric.yaml then.
"""
from __future__ import annotations
import asyncio
import json
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from lab_lib.db import service_session

# Anchor on infra/init.sql — survives layout changes (2026-05-23 fix).
def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "infra" / "init.sql").is_file():
            return p
    return here.parents[4]


REPO_ROOT = _repo_root()
PROD_ROOT = REPO_ROOT  # Legacy alias.
OUT_DIR = REPO_ROOT / "output" / "dogfood"
VAL_DIR = REPO_ROOT / "output" / "validation"
LEARNINGS = REPO_ROOT / "harness" / "ralph" / "LEARNINGS.md"


def _criterion(name: str, passed: bool, value: Any, target: Any, note: str = "") -> dict:
    return {"name": name, "passed": bool(passed), "value": value, "target": target, "note": note}


# ──────────────── A. Functional readiness ────────────────

def crit_1_p2_score_rolling(window: int = 20, threshold: float = 95.0) -> dict:
    """Rolling avg of last N P2 scores must be >= 95%."""
    files = sorted(VAL_DIR.glob("P2-iter*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    scores = []
    for f in files[:window]:
        try:
            scores.append(json.loads(f.read_text()).get("score_pct", 0))
        except Exception:
            continue
    avg = round(statistics.mean(scores), 2) if scores else 0
    return _criterion(
        "1_p2_score_rolling",
        avg >= threshold and len(scores) >= max(5, window // 4),
        {"avg": avg, "n": len(scores)},
        {"avg_min": threshold, "n_min": max(5, window // 4)},
    )


def crit_2_e2e_flake(threshold_pct: float = 10.0) -> dict:
    """Flake rate per flow over the last full P2 run must be <= 10%."""
    files = sorted(VAL_DIR.glob("P2-iter*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return _criterion("2_e2e_flake", False, None, {"per_flow_max_pct": threshold_pct}, "no P2 runs yet")
    d = json.loads(files[0].read_text())
    flake_per = []
    for r in d.get("results", []):
        if r.get("id", "").startswith("P2-E2E"):
            f = r.get("actual", {}).get("flake_rate_pct")
            if f is not None:
                flake_per.append((r["id"], f))
    worst = max((f for _, f in flake_per), default=0)
    return _criterion(
        "2_e2e_flake",
        worst <= threshold_pct,
        {"worst_pct": worst, "per_flow": dict(flake_per)},
        {"per_flow_max_pct": threshold_pct},
    )


async def crit_3_faithfulness(threshold: float = 0.75) -> dict:
    """Mean answer faithfulness over golden 40 must be >= 0.75. Reads from
    output/validation/golden-faithfulness.json which the P1 golden runner
    writes (assumed to exist; if missing, returns SKIP)."""
    f = VAL_DIR / "golden-faithfulness.json"
    if not f.exists():
        return _criterion("3_faithfulness", False, None, {"mean_min": threshold},
                          "golden-faithfulness.json not found — run P1 golden phase first")
    d = json.loads(f.read_text())
    mean = d.get("mean_score", 0)
    return _criterion("3_faithfulness", mean >= threshold, {"mean": mean}, {"mean_min": threshold})


async def crit_4_rls_zero_leaks(days: int = 7) -> dict:
    """No cross-tenant leak detected by verify_rls in the last N days. Scans
    LEARNINGS for pattern=rls_leak entries within the window."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    if not LEARNINGS.exists():
        return _criterion("4_rls_zero_leaks", False, None, {"window_days": days},
                          "LEARNINGS.md missing")
    leaks = []
    for line in LEARNINGS.read_text().splitlines():
        if "pattern=rls_leak" in line and line.startswith("[") and line[1:21] >= cutoff[:20]:
            leaks.append(line[:200])
    return _criterion(
        "4_rls_zero_leaks",
        len(leaks) == 0,
        {"leak_count": len(leaks), "samples": leaks[:3]},
        {"leak_count_max": 0, "window_days": days},
    )


# ──────────────── B. Adoption ────────────────

async def crit_5_dau(min_dau: int = 5, min_days: int = 14, window: int = 28) -> dict:
    """≥ 5 distinct member_ids active per day for ≥ 14 of last 28 days.

    Uses `events` table (kind='query') as the activity source — it carries
    member_id directly. `messages` table has no member_id column (auth context
    is on the conversation, not the message).
    """
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT date(ts) AS d, count(distinct member_id) AS n
            FROM events
            WHERE ts >= now() - make_interval(days => :days)
              AND kind = 'query'
              AND member_id IS NOT NULL
            GROUP BY d
            ORDER BY d DESC
        """), {"days": window})
        rows = [(str(row[0]), int(row[1])) for row in r]
    qualifying_days = sum(1 for _, n in rows if n >= min_dau)
    return _criterion(
        "5_dau",
        qualifying_days >= min_days,
        {"qualifying_days": qualifying_days, "window_days": window, "by_day": rows[:7]},
        {"qualifying_days_min": min_days, "dau_min": min_dau, "window_days": window},
    )


async def crit_6_total_queries(min_total: int = 200, week: int = 4) -> dict:
    """Total user queries in week N (default week 4) >= 200.

    Counts events.kind='query' over the week-N window (events is the canonical
    audit log; messages.role='user' would also work but events ties to member_id
    which makes DAU + query metrics consistent).
    """
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT count(*) FROM events
            WHERE kind = 'query'
              AND ts >= now() - make_interval(days => :start)
              AND ts <  now() - make_interval(days => :end_d)
        """), {"start": week * 7, "end_d": (week - 1) * 7})
        n = r.scalar_one()
    return _criterion("6_total_queries", n >= min_total, {"queries": int(n), "week": week},
                      {"queries_min": min_total})


async def crit_7_avg_turns(min_turns: float = 3.0) -> dict:
    """Mean conversation length (messages per conv_id) >= 3 turns."""
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT avg(c) FROM (
                SELECT conv_id, count(*) AS c FROM messages GROUP BY conv_id
            ) t
        """))
        avg = float(r.scalar_one() or 0)
    return _criterion("7_avg_turns", avg >= min_turns, {"avg_turns": round(avg, 2)},
                      {"avg_turns_min": min_turns})


# ──────────────── C. Quality ────────────────

async def crit_8_thumbs_up(min_rate: float = 0.70) -> dict:
    """Thumbs-up rate on rated assistant messages >= 70%."""
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT
              count(*) FILTER (WHERE (payload->>'rating')::int > 0) AS up,
              count(*) FILTER (WHERE (payload->>'rating')::int < 0) AS dn,
              count(*) FILTER (WHERE payload ? 'rating') AS total
            FROM events WHERE kind = 'feedback.message'
        """))
        row = r.first()
        up = int(row[0] or 0); dn = int(row[1] or 0); total = int(row[2] or 0)
    rate = (up / total) if total else 0
    return _criterion(
        "8_thumbs_up",
        total >= 5 and rate >= min_rate,
        {"up": up, "down": dn, "total_rated": total, "rate": round(rate, 3)},
        {"rate_min": min_rate, "total_rated_min": 5},
    )


def crit_9_nps() -> dict:
    """NPS-style: median of 'would recommend' responses (0-10) >= 7. Reads from
    output/dogfood/nps-week4.json which is manually populated week 4 day 5."""
    f = OUT_DIR / "nps-week4.json"
    if not f.exists():
        return _criterion("9_nps", False, None, {"median_min": 7, "n_min": 6},
                          "nps-week4.json not found — manual Discord poll required week 4")
    d = json.loads(f.read_text())
    scores = d.get("scores", [])
    median = round(statistics.median(scores), 1) if scores else 0
    return _criterion(
        "9_nps",
        len(scores) >= 6 and median >= 7,
        {"median": median, "n": len(scores)},
        {"median_min": 7, "n_min": 6},
    )


def crit_10_no_critical_incidents(days: int = 28) -> dict:
    """Zero pattern=critical_incident entries in LEARNINGS over last 28 days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    if not LEARNINGS.exists():
        return _criterion("10_no_critical_incidents", False, None, {"max": 0, "window_days": days},
                          "LEARNINGS.md missing")
    found = []
    for line in LEARNINGS.read_text().splitlines():
        if "pattern=critical_incident" in line and line.startswith("[") and line[1:21] >= cutoff[:20]:
            found.append(line[:200])
    return _criterion(
        "10_no_critical_incidents",
        len(found) == 0,
        {"count": len(found), "samples": found[:3]},
        {"max": 0, "window_days": days},
    )


# ──────────────── runner ────────────────

async def run_all() -> dict:
    """Run all 10 criteria. Returns dict suitable for JSON snapshot."""
    crits = []
    # Sync first
    crits.append(crit_1_p2_score_rolling())
    crits.append(crit_2_e2e_flake())
    crits.append(crit_9_nps())
    crits.append(crit_10_no_critical_incidents())
    # Async
    crits.append(await crit_3_faithfulness())
    crits.append(await crit_4_rls_zero_leaks())
    crits.append(await crit_5_dau())
    crits.append(await crit_6_total_queries())
    crits.append(await crit_7_avg_turns())
    crits.append(await crit_8_thumbs_up())

    crits.sort(key=lambda c: c["name"])
    all_passed = all(c["passed"] for c in crits)
    summary = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "all_passed": all_passed,
        "passed_count": sum(1 for c in crits if c["passed"]),
        "total_count": len(crits),
        "criteria": crits,
    }
    return summary


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = asyncio.run(run_all())
    snapshot_path = OUT_DIR / f"{datetime.now(timezone.utc).date().isoformat()}.json"
    snapshot_path.write_text(json.dumps(summary, indent=2, default=str))
    json.dump(summary, sys.stdout, indent=2, default=str)
    print()
    sys.exit(0 if summary["all_passed"] else 1)


if __name__ == "__main__":
    main()
