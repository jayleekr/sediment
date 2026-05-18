"""Phase 4 checks — memory consolidation worker output.

Verifies that consolidate_memory.py produced its expected artifacts:
  - decisions with conv_id linkage (proves chat → structured promotion)
  - idempotent dedup (re-running doesn't bloat the table)
  - weekly active member retention metric

These checks are read-only assertions over the live DB; they do NOT run the
worker. Drive separately via `make consolidate` or cron.
"""
from __future__ import annotations
from sqlalchemy import text

from lab_lib.db import service_session


async def check_decisions_have_conv_provenance(spec: dict, **_) -> dict:
    """At least 1 decision row with conv_id != NULL in the last 30 days."""
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT count(*) FROM decisions
            WHERE conv_id IS NOT NULL
              AND created_at >= now() - interval '30 days'
        """))
        n = r.scalar_one()
    return {
        "passed": n >= 1,
        "actual": {"decisions_with_conv_id_last_30d": n},
        "expected": {"min": 1},
        "message": "" if n >= 1 else
                   "no consolidated decisions in last 30d — run `make consolidate`",
    }


async def check_consolidation_idempotent(spec: dict, **_) -> dict:
    """No two decisions inserted in the last 24h share (tenant, topic, conv_id).

    Tight 24h scope keeps the check focused on the consolidate_memory worker's
    immediate behaviour. The decisions table has older pre-worker pollution
    from a different code path (chat-query rows mis-tagged as decisions —
    separate cleanup task, not P4's responsibility).
    """
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT count(*) FROM (
              SELECT tenant_id, topic, conv_id, count(*) AS n
              FROM decisions
              WHERE conv_id IS NOT NULL
                AND created_at >= now() - interval '24 hours'
              GROUP BY tenant_id, topic, conv_id
              HAVING count(*) > 1
            ) dup
        """))
        n_dup = r.scalar_one()
    return {
        "passed": n_dup == 0,
        "actual": {"duplicate_groups": n_dup},
        "expected": {"max": 0},
        "message": "" if n_dup == 0 else
                   f"{n_dup} (tenant,topic,conv) groups have duplicates — dedup broken",
    }


async def check_weekly_active_members(spec: dict, **_) -> dict:
    """Retention metric: distinct members who started a conversation in the
    last 7 days. Threshold = 1 (any usage at all). Reports the actual number
    so we can watch it grow week-over-week.

    Pass criteria intentionally low for Phase 4 — surface the number, don't
    block on it. Phase 5.5 sets the real bar (lab 8명 평균 5쿼리/일 4주).
    """
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT count(DISTINCT user_id) FROM conversations
            WHERE user_id IS NOT NULL
              AND updated_at >= now() - interval '7 days'
        """))
        n = r.scalar_one()
    return {
        "passed": n >= 1,
        "actual": {"distinct_active_members_last_7d": n},
        "expected": {"min": 1, "phase_5_5_target": 8},
        "message": "" if n >= 1 else "no active members in last 7d",
    }
