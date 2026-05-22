"""A/B variant comparison — 7-day window analysis.

Phase 5d of sediment#15. Reads message_signals + message_judge_scores
for the last 7 days, partitioned by `task_tag` prefix:

  task_tag = 'variant:A'  → control
  task_tag = 'variant:B'  → candidate
  task_tag = NULL or anything else → out of experiment

For each cohort, computes:
  - mean / p25 faithfulness
  - thumbs_up rate per turn
  - re_ask rate per turn
  - mean dwell seconds

Outputs a comparison block + a recommendation ("promote B" / "keep A"
/ "inconclusive — extend window"). Decision is human-gated — the script
never flips a flag itself.

Routes A/B via task_tag: the chat client sets a tag based on the active
variant flag, langgraph writes it into messages.task_tag, this script
groups on it.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import logging
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text

from lab_lib.db import service_session


log = logging.getLogger("ab_compare")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


COHORT_SQL = """
WITH cohort AS (
  SELECT m.id, m.tenant_id, m.task_tag, m.ts,
         (SELECT score FROM message_judge_scores j
          WHERE j.message_id = m.id AND j.judge='faithfulness'
          ORDER BY j.created_at DESC LIMIT 1) AS faith,
         (SELECT COUNT(*) FROM message_signals s
          WHERE s.message_id = m.id AND s.kind='thumbs_up') AS thumbs_up,
         (SELECT COUNT(*) FROM message_signals s
          WHERE s.message_id = m.id AND s.kind='thumbs_down') AS thumbs_down,
         (SELECT COUNT(*) FROM message_signals s
          WHERE s.message_id = m.id AND s.kind='re_ask') AS re_asks,
         (SELECT AVG(value) FROM message_signals s
          WHERE s.message_id = m.id AND s.kind='dwell') AS dwell_avg
  FROM messages m
  WHERE m.role = 'assistant'
    AND m.ts >= :cutoff
    AND m.archived = false
    AND m.task_tag LIKE 'variant:%'
)
SELECT task_tag, COUNT(*) AS n,
       AVG(faith) AS faith_mean,
       PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY faith) AS faith_p25,
       AVG(thumbs_up::float) AS thumbs_up_rate,
       AVG(thumbs_down::float) AS thumbs_down_rate,
       AVG(re_asks::float) AS reask_rate,
       AVG(dwell_avg) AS dwell_avg
FROM cohort
GROUP BY task_tag
ORDER BY task_tag
"""


async def collect(days: int = 7) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with service_session() as s:
        r = await s.execute(text(COHORT_SQL), {"cutoff": cutoff})
        rows = [dict(row._mapping) for row in r]
    return {"days": days, "cohorts": rows}


def _round(x, n=3):
    return None if x is None else round(float(x), n)


def recommend(cohorts: list[dict]) -> str:
    """Compare A vs B on faithfulness mean + thumbs_up_rate."""
    by_tag = {c["task_tag"]: c for c in cohorts}
    a = by_tag.get("variant:A")
    b = by_tag.get("variant:B")
    if not a or not b:
        return "inconclusive — need both A and B cohorts in window"
    if a["n"] < 30 or b["n"] < 30:
        return f"inconclusive — sample too small (A={a['n']}, B={b['n']}); extend window"
    # B wins if faithfulness ≥ A by 0.05 AND thumbs_up_rate not worse by > 0.1
    delta_faith = (b["faith_mean"] or 0) - (a["faith_mean"] or 0)
    delta_thumbs = (b["thumbs_up_rate"] or 0) - (a["thumbs_up_rate"] or 0)
    if delta_faith >= 0.05 and delta_thumbs > -0.10:
        return f"promote B — Δfaith={delta_faith:+.3f}, Δthumbs={delta_thumbs:+.3f}"
    if delta_faith <= -0.05:
        return f"keep A — B regressed on faithfulness Δ={delta_faith:+.3f}"
    return f"inconclusive — Δfaith={delta_faith:+.3f}, Δthumbs={delta_thumbs:+.3f}"


async def run(days: int = 7) -> dict:
    data = await collect(days=days)
    cohorts = []
    for c in data["cohorts"]:
        cohorts.append({
            "task_tag": c["task_tag"],
            "n": int(c["n"] or 0),
            "faith_mean": _round(c.get("faith_mean")),
            "faith_p25": _round(c.get("faith_p25")),
            "thumbs_up_rate": _round(c.get("thumbs_up_rate")),
            "thumbs_down_rate": _round(c.get("thumbs_down_rate")),
            "reask_rate": _round(c.get("reask_rate")),
            "dwell_avg": _round(c.get("dwell_avg")),
        })
    rec = recommend(cohorts)
    out = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "window_days": days,
        "cohorts": cohorts,
        "recommendation": rec,
    }
    p = Path("output/ab")
    p.mkdir(parents=True, exist_ok=True)
    f = p / f"compare-{datetime.utcnow():%Y-%m-%d}.json"
    with open(f, "w") as fh:
        json.dump(out, fh, indent=2)
    log.info("ab_compare %s", rec)
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=7)
    args = p.parse_args()
    res = asyncio.run(run(days=args.days))
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
