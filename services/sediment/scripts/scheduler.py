"""Sediment Collection Agent scheduler — APScheduler daemon.

Runs alongside the API uvicorns under supervisord. Schedules:
  - Discord fetch (incremental, per-channel) at config.discord.fetch_schedule
  - Distill (per-source strategy) at config.distill.schedule
  - Daily health canary at config.health_check.schedule
  - Daily cost monitor at config.cost_monitor.schedule

Reads `services/sediment/config/cron.yaml` once at startup. To add channels
or change frequency: edit the YAML + `fly deploy`. (Multi-tenant later moves
this to `tenant_connectors.config.resources` in the DB.)

Operational properties:
  - Idempotent fetch: uses discord watermark (events.payload->>'message_id')
  - Idempotent distill: dedup on (tenant_id, topic, conv_id|NULL)
  - Single-instance via APScheduler's default in-process schedule store.
    If we ever shard to multiple machines, move to a Redis/DB job store.
  - Crash-resistant: supervisord restarts; missed jobs are picked up on the
    next run since both fetch and distill use sliding-window semantics.

Exit codes:
  0  scheduler exited cleanly (SIGTERM)
  1  bad config / startup error
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from lab_lib.logging import configure_logging, get_logger

configure_logging()
log = get_logger("scheduler")

_THIS = Path(__file__).resolve()
_CRON_YAML = _THIS.parent.parent / "config" / "cron.yaml"

# Job functions are imported lazily inside the job wrappers so module-level
# imports stay cheap and a broken job module doesn't kill scheduler boot.


def _load_config() -> dict[str, Any]:
    if not _CRON_YAML.exists():
        log.error("scheduler.config_missing", path=str(_CRON_YAML))
        sys.exit(1)
    with _CRON_YAML.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    if not isinstance(cfg, dict):
        log.error("scheduler.config_malformed", path=str(_CRON_YAML))
        sys.exit(1)
    return cfg


# ---------------------------------------------------------------------------
# Job wrappers — thin, catch exceptions so one failure doesn't kill the loop
# ---------------------------------------------------------------------------

async def _run_discord_fetch_all(channels: list[dict[str, str]]) -> None:
    """Fetch all channels incrementally. Logs per-channel result.

    Sequential (not parallel): keeps DB connection count predictable and
    Discord API rate limit headroom intact. Each channel takes < 2s typically.
    """
    from scripts.discord_fetch import cmd_fetch
    import argparse as _ap

    for ch in channels:
        cid = ch.get("id")
        cname = ch.get("name") or cid
        if not cid:
            continue
        # Synthesize the argparse namespace cmd_fetch expects.
        args = _ap.Namespace(
            channel_id=cid, channel_name=cname,
            limit=100, after=None, incremental=True,
            dry_run=False, list_resources=False,
        )
        try:
            rc = await cmd_fetch(args)
            log.info("scheduler.fetch.done", channel=cname, rc=rc)
        except Exception as e:
            log.warning("scheduler.fetch.error", channel=cname, err=str(e)[:200])


async def _run_distill(since_hours: int) -> None:
    """Run the distill agent over the sliding window."""
    from scripts.distill import run
    try:
        summary = await run(since_hours=since_hours, dry_run=False)
        log.info("scheduler.distill.done",
                 sources=summary.get("sources", 0),
                 decisions=summary.get("decisions", 0),
                 actions=summary.get("actions", 0),
                 artifacts=summary.get("artifacts", 0),
                 flags=len(summary.get("flags") or []))
    except Exception as e:
        log.exception("scheduler.distill.error", err=str(e)[:200])


async def _run_consolidate(tenant: str, since_hours: int, limit: int) -> None:
    """Phase 4 consolidator — extract decisions/actions from recent convs."""
    from scripts.consolidate_memory import run as consolidate_run
    try:
        summary = await consolidate_run(
            tenant=tenant, since_hours=since_hours, limit=limit, dry_run=False,
        )
        log.info("scheduler.consolidate.done",
                 convs=summary.get("convs", 0),
                 decisions=summary.get("decisions", 0),
                 actions=summary.get("actions", 0),
                 skipped=summary.get("skipped", 0))
    except Exception as e:
        log.exception("scheduler.consolidate.error", err=str(e)[:200])


async def _run_health_check(alert_channel_name: str) -> None:
    """Check that each watched channel has had an event in the last 24h.
    Logs (and can post to Discord) channels that have gone silent.

    The "silent" signal isn't necessarily a failure — channels can be
    legitimately quiet — but if ALL channels are silent for 24h, the
    likeliest explanation is a broken fetch path.
    """
    from sqlalchemy import text
    from lab_lib.db import service_session
    silent: list[str] = []
    fresh: list[tuple[str, datetime]] = []
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT payload->>'channel' AS ch, max(ts) AS latest
            FROM events WHERE source='discord'
            GROUP BY 1
        """))
        now = datetime.now(timezone.utc)
        for row in r:
            ch, latest = row[0], row[1]
            if latest is None:
                silent.append(ch)
                continue
            age_h = (now - latest).total_seconds() / 3600
            if age_h > 24:
                silent.append(f"{ch} ({age_h:.0f}h stale)")
            else:
                fresh.append((ch, latest))
    log.info("scheduler.health", silent_count=len(silent),
             fresh_count=len(fresh), silent=silent[:10])
    if not fresh:
        log.warning("scheduler.health.all_silent",
                    alert_channel=alert_channel_name)


async def _run_cost_monitor(daily_budget_usd: float, alert_channel_name: str) -> None:
    """Daily LLM cost rollup from the llm_calls table (real token counts).

    Logs a structured summary; the structured logger will surface this in
    `fly logs` and any log aggregator. Future: post to Discord alert_channel
    when total_cost_usd > daily_budget_usd.
    """
    from lab_lib.cost_tracker import daily_summary
    summary = await daily_summary(days=1)
    over = summary["total_cost_usd"] > daily_budget_usd
    log.info(
        "scheduler.cost.daily",
        calls=summary["total_calls"],
        tokens_in=summary["total_tokens_in"],
        tokens_out=summary["total_tokens_out"],
        cost_usd=summary["total_cost_usd"],
        budget_usd=daily_budget_usd,
        over_budget=over,
        unpriced_calls=summary["unpriced_calls"],
        by_agent=summary["by_agent"],
        by_model=summary["by_model"],
        alert_channel=alert_channel_name if over else None,
    )


# ---------------------------------------------------------------------------
# Scheduler boot
# ---------------------------------------------------------------------------

def _add_cron(scheduler: AsyncIOScheduler, name: str, expr: str, fn, *args) -> None:
    """Add a job with cron expression. Coerce all errors to a log + skip
    so one bad expression doesn't kill the rest of the schedule."""
    try:
        trigger = CronTrigger.from_crontab(expr, timezone=timezone.utc)
        scheduler.add_job(fn, trigger=trigger, args=args, id=name,
                          replace_existing=True, max_instances=1,
                          coalesce=True, misfire_grace_time=300)
        log.info("scheduler.job.scheduled", name=name, cron=expr)
    except Exception as e:
        log.error("scheduler.job.schedule_failed", name=name, cron=expr,
                  err=str(e)[:200])


async def main_async() -> int:
    cfg = _load_config()
    scheduler = AsyncIOScheduler(timezone=timezone.utc)

    # Discord fetch (per-channel, sequential within the job)
    disc = cfg.get("discord") or {}
    channels = disc.get("channels") or []
    if channels:
        _add_cron(scheduler, "discord_fetch", disc.get("fetch_schedule", "*/30 * * * *"),
                  _run_discord_fetch_all, channels)
    else:
        log.warning("scheduler.no_channels_configured")

    # Distill
    dst = cfg.get("distill") or {}
    _add_cron(scheduler, "distill", dst.get("schedule", "5 * * * *"),
              _run_distill, int(dst.get("since_hours", 2)))

    # Consolidate (Phase 4 — chat → decisions/actions)
    cs = cfg.get("consolidate") or {}
    if cs:
        _add_cron(scheduler, "consolidate", cs.get("schedule", "15 */12 * * *"),
                  _run_consolidate,
                  cs.get("tenant", "hypeproof-lab"),
                  int(cs.get("since_hours", 13)),
                  int(cs.get("limit", 50)))

    # Health canary
    hc = cfg.get("health_check") or {}
    _add_cron(scheduler, "health_check", hc.get("schedule", "0 21 * * *"),
              _run_health_check, hc.get("alert_channel_name", "sediment"))

    # Cost monitor
    cm = cfg.get("cost_monitor") or {}
    _add_cron(scheduler, "cost_monitor", cm.get("schedule", "30 21 * * *"),
              _run_cost_monitor, float(cm.get("daily_budget_usd", 5.0)),
              cm.get("alert_channel_name", "sediment"))

    # Boot the scheduler BEFORE introspecting next_run_time — APScheduler
    # only populates next_run_time once the scheduler is running.
    # Graceful shutdown on SIGTERM (supervisord sends this on restart).
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass  # Windows fallback; not reachable in our Linux container

    scheduler.start()
    # Now safe to introspect next_run_time.
    jobs_info = [
        {"id": j.id, "next": str(j.next_run_time)}
        for j in scheduler.get_jobs()
    ]
    log.info("scheduler.started", jobs=jobs_info,
             pid=os.getpid(), config=str(_CRON_YAML))
    try:
        await stop.wait()
    finally:
        scheduler.shutdown(wait=False)
        log.info("scheduler.stopped")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
