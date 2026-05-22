"""Nightly LLM-as-judge cron — scores yesterday's assistant messages.

Phase 3 of sediment#15. Runs at 06:00 KST. Reads assistant messages from
the last 24h, replays each (query, answer, citations) through the
faithfulness judge, writes `message_judge_scores` rows.

Already-scored rows for the same (message_id, judge, rubric_version)
are skipped — idempotent.

Posts a Discord summary at the end:
  - n_judged
  - mean score
  - drift vs 7-day rolling median (alert if drop > 0.5)

Cost guard: hard limit on max queries per run (default 200) — exits
early if exceeded. Estimated cost at limit: ~$1.00.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import logging
import os
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text

from lab_lib.db import service_session
from validator.checks.p2_faithfulness import _judge_one, FaithfulnessResult


log = logging.getLogger("judge_daily")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


RUBRIC_VERSION = "v1"
JUDGE_NAME = "faithfulness"


# ============================================================
# Fetch yesterday's assistant messages
# ============================================================

async def _fetch_targets(hours: int = 24, max_n: int = 200) -> list[dict]:
    """Pull (msg_id, tenant, user_query, answer, citations) for assistant
    messages in last `hours` that don't yet have a score for this judge
    + rubric version."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT
              m.id::text         AS message_id,
              m.tenant_id::text  AS tenant_id,
              m.content          AS answer,
              m.citations        AS citations,
              u.content          AS user_query
            FROM messages m
            JOIN messages u
              ON u.conv_id = m.conv_id
             AND u.role    = 'user'
             AND u.ts      < m.ts
             AND u.ts      = (
               SELECT MAX(ts) FROM messages
               WHERE conv_id = m.conv_id
                 AND role    = 'user'
                 AND ts      < m.ts
             )
            LEFT JOIN message_judge_scores j
              ON j.message_id     = m.id
             AND j.judge          = :judge
             AND j.rubric_version = :rv
            WHERE m.role = 'assistant'
              AND m.ts >= :cutoff
              AND m.archived = false
              AND j.id IS NULL
            ORDER BY m.ts DESC
            LIMIT :lim
        """), {
            "cutoff": cutoff,
            "judge": JUDGE_NAME,
            "rv": RUBRIC_VERSION,
            "lim": max_n,
        })
        rows = []
        for row in r:
            d = dict(row._mapping)
            # citations is jsonb; in asyncpg it's already a python list
            if isinstance(d["citations"], str):
                d["citations"] = json.loads(d["citations"])
            rows.append(d)
        return rows


# ============================================================
# Score + write
# ============================================================

async def _score_and_persist(target: dict, model_name: str) -> FaithfulnessResult:
    result = await _judge_one(target["user_query"], target["answer"], target["citations"])
    # Cost estimate per call — Haiku 4.5 input ~1k + output ~50 tokens.
    # ~$0.005. We'll record 0.005 as a static estimate (we don't have
    # token counting wired in the judge yet — TODO).
    cost = 0.005
    async with service_session() as s:
        await s.execute(text("""
            INSERT INTO message_judge_scores (
                tenant_id, message_id, judge, model, score, raw_score,
                reason, rubric_version, cost_usd
            ) VALUES (
                CAST(:tid AS uuid), CAST(:mid AS uuid),
                :judge, :model, :score, :raw,
                :reason, :rv, :cost
            )
            ON CONFLICT (message_id, judge, rubric_version) DO NOTHING
        """), {
            "tid": target["tenant_id"],
            "mid": target["message_id"],
            "judge": JUDGE_NAME,
            "model": model_name,
            "score": result.score,
            "raw": result.raw_score,
            "reason": result.reason,
            "rv": RUBRIC_VERSION,
            "cost": cost,
        })
    return result


# ============================================================
# Drift detection
# ============================================================

async def _rolling_median(days: int = 7) -> float | None:
    """Median faithfulness over the last N days. None if no data."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT score FROM message_judge_scores
            WHERE judge = :judge AND rubric_version = :rv
              AND created_at >= :cutoff
        """), {"judge": JUDGE_NAME, "rv": RUBRIC_VERSION, "cutoff": cutoff})
        scores = [row[0] for row in r if row[0] is not None]
    if not scores:
        return None
    return statistics.median(scores)


# ============================================================
# Entry point
# ============================================================

async def run(hours: int = 24, max_n: int = 200) -> dict:
    targets = await _fetch_targets(hours=hours, max_n=max_n)
    log.info("judge_daily.start n_targets=%s", len(targets))
    if not targets:
        return {"n_judged": 0, "mean_score": None, "drift_alert": False}

    model_name = os.environ.get("FAITH_JUDGE_MODEL", "claude-haiku-4-5-20251001")
    results: list[FaithfulnessResult] = []
    sem = asyncio.Semaphore(3)

    async def _one(t: dict) -> FaithfulnessResult | None:
        async with sem:
            try:
                return await _score_and_persist(t, model_name)
            except Exception as e:
                log.exception("judge.failure mid=%s err=%s", t["message_id"], e)
                return None

    for r in await asyncio.gather(*[_one(t) for t in targets]):
        if r is not None:
            results.append(r)

    if not results:
        return {"n_judged": 0, "mean_score": None, "drift_alert": False}

    today_median = statistics.median(r.score for r in results)
    rolling = await _rolling_median(days=7)
    drift_alert = (
        rolling is not None and (rolling - today_median) > 0.5
    )

    summary = {
        "n_judged": len(results),
        "median_score_today": round(today_median, 3),
        "rolling_median_7d": round(rolling, 3) if rolling else None,
        "drift_alert": drift_alert,
    }
    log.info("judge_daily.done %s", summary)

    # Dump for the Discord hook to read
    out = Path("output/dogfood")
    out.mkdir(parents=True, exist_ok=True)
    with open(out / f"judge-daily-{datetime.utcnow():%Y-%m-%d}.json", "w") as f:
        json.dump(summary, f, indent=2)
    return summary


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--hours", type=int, default=24)
    p.add_argument("--max", type=int, default=200)
    args = p.parse_args()
    asyncio.run(run(hours=args.hours, max_n=args.max))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
