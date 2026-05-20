"""LLM cost tracking — records every Anthropic call into `llm_calls` table.

Why a real table (not just log lines):
  - Daily/weekly cost summaries need SQL.
  - Per-tenant attribution at SaaS-scale needs a join.
  - Logs rotate; financial-relevant data shouldn't.

Schema is bootstrapped lazily — first call to `record_call` ensures the
table exists. No alembic migration required. When this grows to need
proper per-tenant RLS or a separate billing DB, migrate to that path; for
v1 dogfood + first external tenant, the simple table is enough.

Usage (from consolidate_memory._extract or similar):

    from lab_lib.cost_tracker import record_call

    resp = await client.messages.create(...)
    await record_call(
        model=resp.model,
        agent="distill",
        strategy=strategy.name,
        prompt_version=strategy.prompt_version,
        tokens_in=resp.usage.input_tokens,
        tokens_out=resp.usage.output_tokens,
        tenant_id=tid,
    )
"""
from __future__ import annotations

import asyncio
from typing import Optional

from sqlalchemy import text

from lab_lib.db import service_session
from lab_lib.logging import get_logger

log = get_logger("cost_tracker")


# Anthropic public API prices (USD per million tokens). Update when
# Anthropic adjusts. If a model isn't in this table, cost is recorded as
# NULL and the daily monitor warns "unpriced model: <name>".
#
# Source: https://www.anthropic.com/pricing (verified May 2026).
_PRICING = {
    "claude-haiku-4-5-20251001":   (1.00, 5.00),    # input, output per MTok
    "claude-haiku-4-5":            (1.00, 5.00),    # alias
    "claude-sonnet-4-6":           (3.00, 15.00),
    "claude-sonnet-4-6-20250915":  (3.00, 15.00),   # dated alias
    "claude-opus-4-7":             (15.00, 75.00),
}

_SCHEMA_BOOTSTRAPPED = False
_BOOTSTRAP_LOCK = asyncio.Lock()


async def _ensure_schema() -> None:
    """Idempotent table create. Guarded by an asyncio lock to avoid two
    concurrent first-callers issuing duplicate CREATE TABLE statements."""
    global _SCHEMA_BOOTSTRAPPED
    if _SCHEMA_BOOTSTRAPPED:
        return
    async with _BOOTSTRAP_LOCK:
        if _SCHEMA_BOOTSTRAPPED:
            return
        async with service_session() as s:
            await s.execute(text("""
                CREATE TABLE IF NOT EXISTS llm_calls (
                    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                    ts              timestamptz NOT NULL DEFAULT now(),
                    tenant_id       uuid REFERENCES tenants(id) ON DELETE CASCADE,
                    model           text NOT NULL,
                    agent           text NOT NULL,
                    strategy        text,
                    prompt_version  text,
                    tokens_in       integer NOT NULL,
                    tokens_out      integer NOT NULL,
                    cost_usd        numeric(10, 6),
                    error           text
                );
            """))
            await s.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_llm_calls_ts ON llm_calls (ts DESC)"
            ))
            await s.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_llm_calls_tenant_ts "
                "ON llm_calls (tenant_id, ts DESC)"
            ))
            await s.commit()
        _SCHEMA_BOOTSTRAPPED = True
        log.info("cost_tracker.schema_bootstrapped")


def _compute_cost(model: str, tokens_in: int, tokens_out: int) -> Optional[float]:
    """Return USD cost or None if model is unpriced. Stored as numeric(10,6)
    — 6 decimal places handle Haiku fractional cents."""
    prices = _PRICING.get(model)
    if not prices:
        return None
    p_in, p_out = prices
    return (tokens_in * p_in / 1_000_000) + (tokens_out * p_out / 1_000_000)


async def record_call(
    *,
    model: str,
    agent: str,
    tokens_in: int,
    tokens_out: int,
    strategy: Optional[str] = None,
    prompt_version: Optional[str] = None,
    tenant_id: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """Insert one row into llm_calls. Never raises — cost tracking failure
    must not break the main extraction path. Worst case we lose a bit of
    accounting data and the daily monitor reports it."""
    try:
        await _ensure_schema()
        cost = _compute_cost(model, tokens_in, tokens_out)
        async with service_session() as s:
            await s.execute(text("""
                INSERT INTO llm_calls
                  (tenant_id, model, agent, strategy, prompt_version,
                   tokens_in, tokens_out, cost_usd, error)
                VALUES
                  (:tid, :model, :agent, :strategy, :pv,
                   :tin, :tout, :cost, :err)
            """), {
                "tid": tenant_id,
                "model": model,
                "agent": agent,
                "strategy": strategy,
                "pv": prompt_version,
                "tin": int(tokens_in),
                "tout": int(tokens_out),
                "cost": cost,
                "err": error,
            })
            await s.commit()
        if cost is None:
            log.warning("cost_tracker.unpriced_model", model=model)
    except Exception as e:
        log.warning("cost_tracker.record_failed", err=str(e)[:200])


# ---------------------------------------------------------------------------
# Aggregation queries for the daily monitor
# ---------------------------------------------------------------------------

async def daily_summary(days: int = 1) -> dict:
    """Roll up llm_calls over the last N days.

    Returns dict shape:
      {
        "window_days": N,
        "total_calls": int,
        "total_tokens_in": int,
        "total_tokens_out": int,
        "total_cost_usd": float,
        "by_agent": {agent: {calls, cost_usd}, ...},
        "by_model": {model: {calls, cost_usd}, ...},
        "unpriced_calls": int,
      }
    """
    await _ensure_schema()
    async with service_session() as s:
        # Totals
        r = await s.execute(text("""
            SELECT count(*), sum(tokens_in), sum(tokens_out),
                   sum(cost_usd), count(*) FILTER (WHERE cost_usd IS NULL)
            FROM llm_calls
            WHERE ts >= now() - (interval '1 day' * :d)
        """), {"d": days})
        row = r.first()
        total_calls   = row[0] or 0
        tokens_in     = int(row[1] or 0)
        tokens_out    = int(row[2] or 0)
        total_cost    = float(row[3] or 0.0)
        unpriced      = row[4] or 0

        # By agent
        r = await s.execute(text("""
            SELECT agent, count(*), coalesce(sum(cost_usd), 0)
            FROM llm_calls
            WHERE ts >= now() - (interval '1 day' * :d)
            GROUP BY agent ORDER BY 3 DESC
        """), {"d": days})
        by_agent = {row[0]: {"calls": row[1], "cost_usd": float(row[2])}
                    for row in r}

        # By model
        r = await s.execute(text("""
            SELECT model, count(*), coalesce(sum(cost_usd), 0)
            FROM llm_calls
            WHERE ts >= now() - (interval '1 day' * :d)
            GROUP BY model ORDER BY 3 DESC
        """), {"d": days})
        by_model = {row[0]: {"calls": row[1], "cost_usd": float(row[2])}
                    for row in r}

    return {
        "window_days": days,
        "total_calls": total_calls,
        "total_tokens_in": tokens_in,
        "total_tokens_out": tokens_out,
        "total_cost_usd": round(total_cost, 4),
        "by_agent": by_agent,
        "by_model": by_model,
        "unpriced_calls": unpriced,
    }
