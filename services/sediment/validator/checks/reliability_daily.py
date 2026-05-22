"""Daily reliability monitor.

Provider-free by default: this module reads DB state and local validator
artifacts, emits a stable JSON report, and degrades honestly when DB or prior
validation artifacts are unavailable.

Usage:
    python -m validator.checks.reliability_daily
    python -m validator.checks.reliability_daily --tenant hypeproof-lab --no-write
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text

from lab_lib.db import service_session
from lab_lib.settings import settings
from validator.checks.p2_grounding import (
    check_citation_hard_gate_contract,
    check_claim_grounding_contract,
    check_no_evidence_fail_closed_contract,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
OUTPUT_DIR = REPO_ROOT / "output" / "reliability"
VALIDATION_DIR = REPO_ROOT / "output" / "validation"
GOLDEN_QUERIES = Path(__file__).resolve().parents[1] / "golden_queries.yaml"

DEFAULT_SLOS = {
    "vault_sync_max_age_hours": 30,
    "artifact_update_max_age_hours": 30,
    "chunk_update_max_age_hours": 30,
    "claim_grounding_min_score": 0.75,
}
SEVERITY_ORDER = {"info": 0, "minor": 1, "major": 2, "critical": 3}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: Any) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    return str(dt)


def age_hours(dt: Any, now: datetime | None = None) -> float | None:
    if dt is None:
        return None
    now = now or utc_now()
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return round((now - dt.astimezone(timezone.utc)).total_seconds() / 3600, 2)
    return None


def warning(code: str, severity: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "severity": severity, "message": message, "details": details}


def report_status(warnings: list[dict[str, Any]], sections: dict[str, Any]) -> str:
    if any(w["severity"] == "critical" for w in warnings):
        return "critical"
    if warnings or any(s.get("status") == "unavailable" for s in sections.values() if isinstance(s, dict)):
        return "degraded"
    return "ok"


async def _scalar(session, sql: str, params: dict[str, Any]) -> Any:
    result = await session.execute(text(sql), params)
    return result.scalar()


async def collect_db_snapshot(tenant_slug: str, since_hours: int = 24) -> dict[str, Any]:
    """Collect freshness and distill state.

    This function never raises. DB/provider outages are represented as an
    unavailable snapshot so daily automation can still produce a report.
    """
    try:
        async with service_session() as session:
            tenant_id = await _scalar(
                session,
                "SELECT id::text FROM tenants WHERE slug = :slug",
                {"slug": tenant_slug},
            )
            if not tenant_id:
                return {
                    "available": False,
                    "tenant_slug": tenant_slug,
                    "error": "tenant_not_found",
                }

            params = {"tenant_id": tenant_id, "since_hours": since_hours}
            latest_vault_sync = await _scalar(
                session,
                """
                SELECT max(ts) FROM events
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND kind IN ('vault.ingest', 'vault.sync')
                """,
                params,
            )
            latest_artifact = await _scalar(
                session,
                "SELECT max(updated_at) FROM artifacts WHERE tenant_id = CAST(:tenant_id AS uuid)",
                params,
            )
            latest_chunk = await _scalar(
                session,
                "SELECT max(created_at) FROM chunks WHERE tenant_id = CAST(:tenant_id AS uuid)",
                params,
            )
            latest_decision = await _scalar(
                session,
                "SELECT max(created_at) FROM decisions WHERE tenant_id = CAST(:tenant_id AS uuid)",
                params,
            )
            artifact_count = int(await _scalar(
                session,
                "SELECT count(*) FROM artifacts WHERE tenant_id = CAST(:tenant_id AS uuid)",
                params,
            ) or 0)
            chunk_count = int(await _scalar(
                session,
                "SELECT count(*) FROM chunks WHERE tenant_id = CAST(:tenant_id AS uuid)",
                params,
            ) or 0)
            artifact_without_chunks = int(await _scalar(
                session,
                """
                SELECT count(*)
                FROM artifacts a
                WHERE a.tenant_id = CAST(:tenant_id AS uuid)
                  AND NOT EXISTS (
                    SELECT 1 FROM chunks c WHERE c.artifact_id = a.id
                  )
                """,
                params,
            ) or 0)

            distill_events_seen = int(await _scalar(
                session,
                """
                SELECT count(*) FROM events
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND ts >= now() - make_interval(hours => :since_hours)
                  AND source = 'discord'
                  AND kind = 'message'
                """,
                params,
            ) or 0)
            decisions_extracted = int(await _scalar(
                session,
                """
                SELECT count(*) FROM decisions
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND created_at >= now() - make_interval(hours => :since_hours)
                """,
                params,
            ) or 0)
            actions_extracted = int(await _scalar(
                session,
                """
                SELECT count(*) FROM actions
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND created_at >= now() - make_interval(hours => :since_hours)
                """,
                params,
            ) or 0)
            decision_artifacts_created = int(await _scalar(
                session,
                """
                SELECT count(*) FROM artifacts
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND type = 'decision'
                  AND updated_at >= now() - make_interval(hours => :since_hours)
                """,
                params,
            ) or 0)
            decisions_with_source_artifact = int(await _scalar(
                session,
                """
                SELECT count(*) FROM decisions
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND created_at >= now() - make_interval(hours => :since_hours)
                  AND source_artifact_id IS NOT NULL
                """,
                params,
            ) or 0)

            return {
                "available": True,
                "tenant_slug": tenant_slug,
                "tenant_id": tenant_id,
                "freshness": {
                    "latest_vault_sync_at": latest_vault_sync,
                    "latest_artifact_update_at": latest_artifact,
                    "latest_chunk_update_at": latest_chunk,
                    "latest_decision_at": latest_decision,
                    "artifact_count": artifact_count,
                    "chunk_count": chunk_count,
                    "artifact_without_chunks": artifact_without_chunks,
                },
                "distill": {
                    "window_hours": since_hours,
                    "events_seen": distill_events_seen,
                    "decisions_extracted": decisions_extracted,
                    "actions_extracted": actions_extracted,
                    "decision_artifacts_created": decision_artifacts_created,
                    "decisions_with_source_artifact": decisions_with_source_artifact,
                },
            }
    except Exception as exc:
        return {
            "available": False,
            "tenant_slug": tenant_slug,
            "error": type(exc).__name__,
            "message": str(exc)[:300],
        }


def summarize_freshness(snapshot: dict[str, Any], now: datetime, slos: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not snapshot.get("available"):
        return {"status": "unavailable", "error": snapshot.get("error")}, [
            warning("db_unavailable", "major", "DB unavailable; freshness could not be measured", error=snapshot.get("error"))
        ]

    raw = snapshot["freshness"]
    signals = {
        "vault_sync": raw.get("latest_vault_sync_at"),
        "artifact_update": raw.get("latest_artifact_update_at"),
        "chunk_update": raw.get("latest_chunk_update_at"),
        "decision": raw.get("latest_decision_at"),
    }
    latest = {
        name: {"at": iso(value), "age_hours": age_hours(value, now)}
        for name, value in signals.items()
    }
    warnings: list[dict[str, Any]] = []
    for signal_name, slo_key in (
        ("vault_sync", "vault_sync_max_age_hours"),
        ("artifact_update", "artifact_update_max_age_hours"),
        ("chunk_update", "chunk_update_max_age_hours"),
    ):
        age = latest[signal_name]["age_hours"]
        max_age = float(slos[slo_key])
        if age is None:
            warnings.append(warning(f"{signal_name}_missing", "major", f"{signal_name} has no timestamp"))
        elif age > max_age:
            warnings.append(warning(
                f"{signal_name}_stale",
                "major",
                f"{signal_name} is older than SLO",
                age_hours=age,
                max_age_hours=max_age,
            ))

    if raw.get("artifact_count", 0) > 0 and raw.get("chunk_count", 0) == 0:
        warnings.append(warning("chunks_missing", "critical", "Artifacts exist but no chunks are indexed"))
    elif raw.get("artifact_without_chunks", 0) > 0:
        warnings.append(warning(
            "artifact_without_chunks",
            "major",
            "Some artifacts have no chunks",
            count=raw["artifact_without_chunks"],
        ))

    summary = {
        "status": "ok" if not warnings else "degraded",
        "signals": latest,
        "counts": {
            "artifacts": raw.get("artifact_count", 0),
            "chunks": raw.get("chunk_count", 0),
            "artifact_without_chunks": raw.get("artifact_without_chunks", 0),
        },
        "slos": slos,
    }
    return summary, warnings


def _load_latest_phase_report(phase_id: str) -> dict[str, Any] | None:
    path = VALIDATION_DIR / f"{phase_id}-latest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def summarize_recall() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    warnings: list[dict[str, Any]] = []
    golden_count = 0
    try:
        golden_count = len((yaml.safe_load(GOLDEN_QUERIES.read_text()) or {}).get("queries") or [])
    except Exception:
        warnings.append(warning("golden_queries_unreadable", "minor", "Golden query file could not be read"))

    p1 = _load_latest_phase_report("P1")
    if not p1:
        warnings.append(warning("recall_report_missing", "major", "No P1-latest validator report found"))
        return {
            "status": "unavailable",
            "golden_query_count": golden_count,
            "latest_report": None,
            "note": "run validator P1 to populate recall regression metrics",
        }, warnings

    recall_checks = [
        r for r in p1.get("results", [])
        if str(r.get("id", "")).startswith("P1-GOLDEN-RAG")
    ]
    failed = [r.get("id") for r in recall_checks if not r.get("passed")]
    if failed:
        warnings.append(warning("recall_regression", "critical", "One or more golden recall checks failed", failed=failed))

    return {
        "status": "ok" if not failed else "critical",
        "golden_query_count": golden_count,
        "latest_report": {
            "timestamp": p1.get("timestamp"),
            "score_pct": p1.get("score_pct"),
            "checks": [
                {
                    "id": r.get("id"),
                    "passed": r.get("passed"),
                    "actual": r.get("actual"),
                    "expected": r.get("expected"),
                }
                for r in recall_checks
            ],
        },
    }, warnings


def summarize_grounding(slos: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    warnings: list[dict[str, Any]] = []
    checks: dict[str, Any] = {}
    try:
        checks["citation_hard_gate"] = check_citation_hard_gate_contract({})
        checks["zero_evidence_fail_closed"] = check_no_evidence_fail_closed_contract({})
        checks["claim_grounding"] = check_claim_grounding_contract(
            {"threshold": slos["claim_grounding_min_score"]}
        )
    except Exception as exc:
        warnings.append(warning("grounding_check_error", "critical", "Grounding contract check failed", error=str(exc)[:300]))
        return {"status": "critical", "checks": checks}, warnings

    failed = [name for name, result in checks.items() if not result.get("passed")]
    if failed:
        warnings.append(warning("grounding_contract_failed", "critical", "Grounding contract failed", failed=failed))
    provider_keys = {
        "anthropic": bool(os.getenv("ANTHROPIC_API_KEY") or settings.anthropic_api_key),
        "openai": bool(os.getenv("OPENAI_API_KEY") or settings.openai_api_key),
        "gemini": bool(os.getenv("GEMINI_API_KEY") or settings.gemini_api_key),
    }
    return {
        "status": "ok" if not failed else "critical",
        "checks": checks,
        "llm_judge": {
            "enabled": False,
            "skipped": True,
            "reason": "daily monitor uses deterministic provider-free checks",
            "provider_keys_present": provider_keys,
        },
    }, warnings


def summarize_distill(snapshot: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not snapshot.get("available"):
        return {"status": "unavailable", "error": snapshot.get("error")}, [
            warning("db_unavailable_distill", "major", "DB unavailable; distill state could not be measured", error=snapshot.get("error"))
        ]
    distill = snapshot["distill"]
    warnings: list[dict[str, Any]] = []
    if distill["events_seen"] > 0 and distill["decisions_extracted"] == 0:
        warnings.append(warning(
            "distill_zero_decisions",
            "major",
            "Discord events were seen but no decisions were extracted",
            events_seen=distill["events_seen"],
        ))
    if distill["decisions_extracted"] > 0 and distill["decision_artifacts_created"] == 0:
        warnings.append(warning(
            "decision_artifacts_missing",
            "major",
            "Decisions were extracted but no decision artifacts were created",
            decisions_extracted=distill["decisions_extracted"],
        ))

    return {
        "status": "ok" if not warnings else "degraded",
        **distill,
    }, warnings


async def build_report(
    tenant_slug: str | None = None,
    since_hours: int = 24,
    slos: dict[str, Any] | None = None,
    write: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    tenant_slug = tenant_slug or settings.default_tenant_slug
    slos = {**DEFAULT_SLOS, **(slos or {})}
    now = now or utc_now()

    snapshot = await collect_db_snapshot(tenant_slug, since_hours=since_hours)
    freshness, freshness_warnings = summarize_freshness(snapshot, now, slos)
    recall, recall_warnings = summarize_recall()
    grounding, grounding_warnings = summarize_grounding(slos)
    distill, distill_warnings = summarize_distill(snapshot)

    warnings = freshness_warnings + recall_warnings + grounding_warnings + distill_warnings
    sections = {
        "freshness": freshness,
        "recall": recall,
        "grounding": grounding,
        "distill": distill,
    }
    report = {
        "schema_version": 1,
        "generated_at": iso(now),
        "tenant_slug": tenant_slug,
        "window_hours": since_hours,
        "status": report_status(warnings, sections),
        "sections": sections,
        "warnings": sorted(warnings, key=lambda w: SEVERITY_ORDER.get(w["severity"], 0), reverse=True),
        "notification": {
            "event_type": "reliability.daily",
            "tenant_slug": tenant_slug,
            "payload": {
                "status": None,
                "warning_count": None,
                "critical_count": None,
                "major_count": None,
                "report_path": None,
            },
        },
    }

    if write:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        safe_tenant = tenant_slug.replace("/", "_")
        path = OUTPUT_DIR / f"{now.date().isoformat()}-{safe_tenant}.json"
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        latest = OUTPUT_DIR / f"latest-{safe_tenant}.json"
        try:
            if latest.is_symlink() or latest.exists():
                latest.unlink()
            latest.symlink_to(path.name)
        except OSError:
            latest.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        report["path"] = str(path)

    notification_payload = report["notification"]["payload"]
    notification_payload["status"] = report["status"]
    notification_payload["warning_count"] = len(report["warnings"])
    notification_payload["critical_count"] = sum(1 for w in report["warnings"] if w["severity"] == "critical")
    notification_payload["major_count"] = sum(1 for w in report["warnings"] if w["severity"] == "major")
    notification_payload["report_path"] = report.get("path", "")
    return report


def _exit_code(report: dict[str, Any]) -> int:
    if report["status"] == "critical":
        return 2
    if report["status"] == "degraded":
        return 1
    return 0


async def main_async(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit daily Sediment reliability report JSON")
    parser.add_argument("--tenant", default=settings.default_tenant_slug)
    parser.add_argument("--since-hours", type=int, default=24)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--strict-exit", action="store_true", help="exit non-zero on degraded or critical status")
    args = parser.parse_args(argv)

    report = await build_report(args.tenant, since_hours=args.since_hours, write=not args.no_write)
    json.dump(report, sys.stdout, indent=2, ensure_ascii=False, default=str)
    print()
    return _exit_code(report) if args.strict_exit else 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
