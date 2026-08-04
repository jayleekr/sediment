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

async def _discord_targets(yaml_channels: list[dict[str, str]]) -> list[dict]:
    """Which (tenant, channel) pairs to fetch this tick (sediment#163).

    Tenant data wins: every tenant with an `integrations` row of kind='discord'
    contributes the channels it opted into via `config.resources`, exactly as
    the GitHub connector already works.

    config/cron.yaml is the FALLBACK, not the source of truth. It stays because
    removing it would silently stop capture on the current deployment, which
    has no discord integration row yet — a config migration that turns off
    ingestion is worse than a duplicated list. The fallback logs loudly so the
    state is visible rather than assumed.
    """
    from scripts.discord_fetch import channels_from_config, load_discord_integrations

    try:
        integrations = await load_discord_integrations()
    except Exception as e:
        log.warning("scheduler.discord.integrations_unreadable",
                    err=str(e)[:200], fallback="cron.yaml")
        integrations = []

    targets: list[dict] = []
    for integ in integrations:
        for ch in channels_from_config(integ["config"]):
            targets.append({
                "tenant_id": integ["tenant_id"],
                "integration_id": integ["id"],
                "integration_config": integ["config"],
                "channel": ch,
            })

    if targets:
        return targets

    log.warning(
        "scheduler.discord.no_db_config — falling back to config/cron.yaml. "
        "Add an integrations row (kind='discord', config.resources) to make "
        "channel opt-in tenant data (sediment#163).",
        channels=len(yaml_channels))
    return [
        {"tenant_id": None, "integration_id": None, "integration_config": {},
         "channel": ch}
        for ch in yaml_channels if ch.get("id")
    ]


async def _run_discord_fetch_all(channels: list[dict[str, str]]) -> None:
    """Fetch every opted-in channel of every tenant, incrementally.

    Sequential (not parallel): keeps DB connection count predictable and
    Discord API rate limit headroom intact. Each channel takes < 2s typically.
    """
    from scripts.discord_fetch import cmd_fetch
    import argparse as _ap

    for target in await _discord_targets(channels):
        ch = target["channel"]
        cid, cname = ch["id"], ch.get("name") or ch["id"]
        # Synthesize the argparse namespace cmd_fetch expects.
        args = _ap.Namespace(
            channel_id=cid, channel_name=cname,
            limit=100, after=None, incremental=True,
            dry_run=False, list_resources=False,
            tenant_id=target["tenant_id"],
            integration_id=target["integration_id"],
            integration_config=target["integration_config"],
        )
        try:
            rc = await cmd_fetch(args)
            log.info("scheduler.fetch.done", channel=cname,
                     tenant=target["tenant_id"], rc=rc)
        except Exception as e:
            log.warning("scheduler.fetch.error", channel=cname,
                        tenant=target["tenant_id"], err=str(e)[:200])


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


async def _run_github_repo_sync() -> None:
    """Pull every tenant's github integration (kind='github'). One pass
    advances watermarks for all configured tenants."""
    from scripts.github_repo_fetch import amain as gh_amain
    try:
        rc = await gh_amain(["--all"])
        log.info("scheduler.github_repo_sync.done", rc=rc)
    except Exception as e:
        log.exception("scheduler.github_repo_sync.error", err=str(e)[:200])


# ============================================================
# Self-improving RAG handlers (sediment#15 Phases 2-5)
# Each one is a thin call into the matching scripts/*.py module.
# Failures get logged + swallowed — never bring down the scheduler.
# ============================================================

async def _run_signal_derivation(window_sec: int) -> None:
    from scripts.signal_derivation import run as sd_run
    try:
        summary = await sd_run(window_sec=window_sec)
        log.info("scheduler.signal_derivation.done", **summary)
    except Exception as e:
        log.exception("scheduler.signal_derivation.error", err=str(e)[:200])


async def _run_judge_daily(hours: int, max_n: int) -> None:
    from scripts.judge_daily import run as jd_run
    try:
        summary = await jd_run(hours=hours, max_n=max_n)
        log.info("scheduler.judge_daily.done", **summary)
    except Exception as e:
        log.exception("scheduler.judge_daily.error", err=str(e)[:200])


async def _run_hard_negative_mining(window_hours: int) -> None:
    from scripts.hard_negative_mining import run as hnm_run
    try:
        summary = await hnm_run(hours=window_hours)
        log.info("scheduler.hard_negative_mining.done", **summary)
    except Exception as e:
        log.exception("scheduler.hard_negative_mining.error", err=str(e)[:200])


async def _run_chunking_ablation(corpus_n: int, queries_n: int) -> None:
    from scripts.chunking_ablation import run as ca_run
    try:
        summary = await ca_run(corpus_n=corpus_n, queries_n=queries_n)
        # Keep the log compact — ablation result has nested grid arrays
        log.info(
            "scheduler.chunking_ablation.done",
            corpus_size=summary.get("corpus_size"),
            front_size=len(summary.get("pareto_front") or []),
        )
    except Exception as e:
        log.exception("scheduler.chunking_ablation.error", err=str(e)[:200])


async def _run_ab_compare(days: int) -> None:
    from scripts.ab_compare import run as ab_run
    try:
        summary = await ab_run(days=days)
        log.info("scheduler.ab_compare.done", recommendation=summary.get("recommendation"))
    except Exception as e:
        log.exception("scheduler.ab_compare.error", err=str(e)[:200])


async def _run_dspy_bootstrap(max_iters: int) -> None:
    """Synchronous run() in dspy_bootstrap — wrap to avoid blocking the loop."""
    import asyncio as _asyncio
    from scripts.dspy_bootstrap import run as dspy_run
    try:
        loop = _asyncio.get_event_loop()
        result = await loop.run_in_executor(None, dspy_run, max_iters)
        log.info("scheduler.dspy_bootstrap.done", status=result.get("status"))
    except Exception as e:
        log.exception("scheduler.dspy_bootstrap.error", err=str(e)[:200])


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


async def _run_retention_sweep() -> None:
    """Daily archive + purge per design doc 15. Idempotent."""
    from scripts.retention_sweep import amain as ret_amain
    try:
        rc = await ret_amain([])
        log.info("scheduler.retention_sweep.done", rc=rc)
    except Exception as e:
        log.exception("scheduler.retention_sweep.error", err=str(e)[:200])


async def _run_cost_monitor(daily_budget_usd: float, alert_channel_name: str) -> None:
    """Daily LLM cost rollup from the llm_calls table (real token counts).

    Logs a structured summary and, when total_cost_usd > daily_budget_usd,
    fires a `cost.over_budget` notification through the vendored notify
    module (Discord webhook via routes.yaml).
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
    if over:
        await _send_notify("cost.over_budget", tenant_slug="hypeproof-lab", payload={
            "total_cost_usd": summary["total_cost_usd"],
            "daily_budget_usd": daily_budget_usd,
            "by_agent": summary.get("by_agent") or {},
            "by_model": summary.get("by_model") or {},
            "unpriced_calls": summary.get("unpriced_calls") or 0,
        })


async def _run_daily_digest(tenant_slug: str = "hypeproof-lab") -> None:
    """Per-tenant 09:00 KST digest of yesterday's activity → primary channel.

    Pulls counts from the DB (chat queries, new decisions/actions, ingested
    artifacts) and the cost summary, renders via daily_digest template,
    fires through notify. Idempotent — same data twice = same render.
    """
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import text
    from lab_lib.db import service_session
    from lab_lib.cost_tracker import daily_summary

    yesterday = (datetime.now(tz=timezone.utc) - timedelta(days=1)).date()
    async with service_session() as s:
        tid_row = await s.execute(text(
            "SELECT id::text FROM tenants WHERE slug = :s"), {"s": tenant_slug})
        tid = (tid_row.first() or [None])[0]
        if not tid:
            log.warning("scheduler.digest.skip", reason="tenant not found", slug=tenant_slug)
            return

        # Counts for yesterday
        async def _scalar(q: str) -> int:
            r = await s.execute(text(q), {"t": tid, "since": yesterday})
            return int((r.scalar() or 0))

        chat_count = await _scalar(
            "SELECT count(*) FROM events WHERE tenant_id=CAST(:t AS uuid) "
            "AND kind='query' AND ts::date = :since"
        )
        new_decisions = await _scalar(
            "SELECT count(*) FROM decisions WHERE tenant_id=CAST(:t AS uuid) "
            "AND created_at::date = :since"
        )
        new_actions = await _scalar(
            "SELECT count(*) FROM actions WHERE tenant_id=CAST(:t AS uuid) "
            "AND created_at::date = :since"
        )
        ingested = await _scalar(
            "SELECT count(*) FROM artifacts WHERE tenant_id=CAST(:t AS uuid) "
            "AND updated_at::date = :since"
        )

    cost = await daily_summary(days=1)
    payload = {
        "date": yesterday.isoformat(),
        "chat_count": chat_count,
        "new_decisions": new_decisions,
        "new_actions": new_actions,
        "ingested_artifacts": ingested,
        "cost_usd": cost.get("total_cost_usd") or 0.0,
        "budget_usd": 5.0,
    }
    if chat_count == 0 and new_decisions == 0 and ingested == 0:
        log.info("scheduler.digest.skip", reason="no activity", slug=tenant_slug)
        return
    await _send_notify("daily.digest", tenant_slug=tenant_slug, payload=payload)


async def _run_reliability_daily(
    tenant_slug: str = "hypeproof-lab",
    since_hours: int = 24,
    notify: str = "warning",
) -> None:
    """Daily freshness/recall/grounding/distill monitor.

    The monitor itself is provider-free and never raises for DB/provider
    outages; those become degraded JSON sections and warnings.
    """
    from validator.checks.reliability_daily import build_report

    try:
        report = await build_report(tenant_slug=tenant_slug, since_hours=since_hours, write=True)
        warnings = report.get("warnings") or []
        log.info(
            "scheduler.reliability.daily",
            tenant=tenant_slug,
            status=report.get("status"),
            warnings=len(warnings),
            path=report.get("path"),
        )
        should_notify = notify == "always" or (notify == "warning" and warnings)
        if should_notify:
            await _send_notify(
                report["notification"]["event_type"],
                tenant_slug=tenant_slug,
                payload={
                    **report["notification"]["payload"],
                    "warnings": warnings[:5],
                },
            )
    except Exception as e:
        log.exception("scheduler.reliability.error", tenant=tenant_slug, err=str(e)[:200])


async def _send_notify(event_type: str, tenant_slug: str, payload: dict) -> None:
    """Thin wrapper around the vendored notify CLI. Never raises — logs and
    swallows because notification failure must not break the cron job.

    Uses the CLI (not the Python API) so we don't have to import the
    vendored module — `scripts/notify/` lives one level up from this file
    and isn't on sys.path. Subprocess avoids that gymnastics.
    """
    import asyncio as _asyncio
    import json as _json
    from pathlib import Path as _Path

    notify_py = _Path(__file__).resolve().parents[3] / "scripts" / "notify" / "notify.py"
    routes = _Path(__file__).resolve().parents[1] / "config" / "notify_routes.yaml"
    if not notify_py.is_file() or not routes.is_file():
        log.warning("notify.skip", reason="notify.py or routes.yaml missing",
                    notify=str(notify_py), routes=str(routes))
        return

    cmd = ["python3", str(notify_py), "send", event_type,
           "--routes", str(routes), "--tenant", tenant_slug]
    for k, v in (payload or {}).items():
        # JSON-encode complex values via the `key:=value` form the CLI supports.
        if isinstance(v, (dict, list)):
            cmd += ["--data", f"{k}:={_json.dumps(v)}"]
        else:
            cmd += ["--data", f"{k}={v}"]
    try:
        proc = await _asyncio.create_subprocess_exec(
            *cmd, stdout=_asyncio.subprocess.PIPE, stderr=_asyncio.subprocess.PIPE,
        )
        out, err = await _asyncio.wait_for(proc.communicate(), timeout=20.0)
        log.info("notify.sent", event=event_type, slug=tenant_slug,
                 rc=proc.returncode, out=(out or b"").decode()[:200])
    except Exception as e:
        log.warning("notify.failed", event=event_type, slug=tenant_slug,
                    err=str(e)[:200])


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


async def _register(scheduler: AsyncIOScheduler, cfg: dict) -> None:
    """Wire every cron.yaml entry into the scheduler.

    Extracted from main_async() so tests can verify which jobs land
    without booting the scheduler loop. See tests/test_scheduler_registration.py.
    """
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

    # GitHub repo sync (every tenant with a github integration row)
    gh = cfg.get("github_repo_sync") or {}
    if gh.get("enabled", True):
        _add_cron(scheduler, "github_repo_sync",
                  gh.get("schedule", "0 0-13 * * *"),  # 09-22 KST hourly
                  _run_github_repo_sync)

    # Health canary
    hc = cfg.get("health_check") or {}
    _add_cron(scheduler, "health_check", hc.get("schedule", "0 21 * * *"),
              _run_health_check, hc.get("alert_channel_name", "sediment"))

    # Cost monitor
    cm = cfg.get("cost_monitor") or {}
    _add_cron(scheduler, "cost_monitor", cm.get("schedule", "30 21 * * *"),
              _run_cost_monitor, float(cm.get("daily_budget_usd", 5.0)),
              cm.get("alert_channel_name", "sediment"))

    # Retention sweep (design doc 15) — archive inactive convs, purge expired
    rs = cfg.get("retention_sweep") or {}
    if rs.get("enabled", True):
        _add_cron(scheduler, "retention_sweep",
                  rs.get("schedule", "0 4 * * *"),  # 13:00 KST
                  _run_retention_sweep)

    # Daily digest — 09:00 KST = 00:00 UTC. Per-tenant; v1 loops over a
    # static list in cron.yaml. Move to DB-driven `tenants WHERE
    # feature_flags.digest_enabled = true` when > 5 tenants.
    dg = cfg.get("daily_digest") or {}
    if dg.get("enabled", True):
        for tenant in (dg.get("tenants") or ["hypeproof-lab"]):
            _add_cron(
                scheduler, f"daily_digest_{tenant}",
                dg.get("schedule", "0 0 * * *"),
                _run_daily_digest, tenant,
            )

    # Daily reliability monitor — 08:30 KST by default, before the 09:00 digest.
    rel = cfg.get("reliability_daily") or {}
    if rel.get("enabled", True):
        for tenant in (rel.get("tenants") or ["hypeproof-lab"]):
            _add_cron(
                scheduler, f"reliability_daily_{tenant}",
                rel.get("schedule", "30 23 * * *"),
                _run_reliability_daily,
                tenant,
                int(rel.get("since_hours", 24)),
                rel.get("notify", "warning"),
            )

    # ─── Self-improving RAG (sediment#15 Phases 2-5) ─────────────────────
    # Each section reads its own cron.yaml block. enabled:false (default
    # for dspy) skips wiring entirely. Every handler swallows exceptions
    # internally so a broken job never takes the scheduler down.

    sd = cfg.get("signal_derivation") or {}
    if sd.get("enabled", True):
        # YAML window is "30m" / "1h" — parse to seconds.
        w = str(sd.get("window", "30m"))
        unit = w[-1]; val = int(w[:-1])
        w_sec = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit] * val
        _add_cron(scheduler, "signal_derivation",
                  sd.get("schedule", "*/15 * * * *"),
                  _run_signal_derivation, w_sec)

    jd = cfg.get("judge_daily") or {}
    if jd.get("enabled", True):
        _add_cron(scheduler, "judge_daily",
                  jd.get("schedule", "0 21 * * *"),
                  _run_judge_daily,
                  int(jd.get("hours", 24)),
                  int(jd.get("max_n", 200)))

    hnm = cfg.get("hard_negative_mining") or {}
    if hnm.get("enabled", True):
        _add_cron(scheduler, "hard_negative_mining",
                  hnm.get("schedule", "0 18 * * 0"),
                  _run_hard_negative_mining,
                  int(hnm.get("window_hours", 168)))

    ca = cfg.get("chunking_ablation") or {}
    if ca.get("enabled", True):
        _add_cron(scheduler, "chunking_ablation",
                  ca.get("schedule", "0 18 * * 1"),
                  _run_chunking_ablation,
                  int(ca.get("corpus_n", 50)),
                  int(ca.get("queries_n", 20)))

    ab = cfg.get("ab_compare") or {}
    if ab.get("enabled", True):
        _add_cron(scheduler, "ab_compare",
                  ab.get("schedule", "0 18 * * 2"),
                  _run_ab_compare,
                  int(ab.get("days", 7)))

    dspy = cfg.get("dspy_bootstrap") or {}
    if dspy.get("enabled", False):  # opt-in (deps heavy)
        _add_cron(scheduler, "dspy_bootstrap",
                  dspy.get("schedule", "0 18 * * 3"),
                  _run_dspy_bootstrap,
                  int(dspy.get("max_iters", 6)))


async def main_async() -> int:
    cfg = _load_config()
    scheduler = AsyncIOScheduler(timezone=timezone.utc)
    await _register(scheduler, cfg)

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
