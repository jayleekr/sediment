from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from validator.checks import reliability_daily as rd


def _snapshot(now: datetime) -> dict:
    return {
        "available": True,
        "tenant_slug": "hypeproof-lab",
        "tenant_id": "tenant-1",
        "freshness": {
            "latest_vault_sync_at": now - timedelta(hours=2),
            "latest_artifact_update_at": now - timedelta(hours=3),
            "latest_chunk_update_at": now - timedelta(hours=4),
            "latest_decision_at": now - timedelta(hours=5),
            "artifact_count": 12,
            "chunk_count": 120,
            "artifact_without_chunks": 0,
        },
        "distill": {
            "window_hours": 24,
            "events_seen": 8,
            "decisions_extracted": 2,
            "actions_extracted": 1,
            "decision_artifacts_created": 2,
            "decisions_with_source_artifact": 2,
        },
    }


def test_freshness_flags_artifact_chunk_mismatch():
    now = datetime(2026, 5, 22, tzinfo=timezone.utc)
    snapshot = _snapshot(now)
    snapshot["freshness"]["artifact_without_chunks"] = 3

    summary, warnings = rd.summarize_freshness(snapshot, now, rd.DEFAULT_SLOS)

    assert summary["status"] == "degraded"
    assert summary["counts"]["artifact_without_chunks"] == 3
    assert any(w["code"] == "artifact_without_chunks" for w in warnings)


def test_freshness_critical_when_artifacts_exist_without_chunks():
    now = datetime(2026, 5, 22, tzinfo=timezone.utc)
    snapshot = _snapshot(now)
    snapshot["freshness"]["chunk_count"] = 0

    _, warnings = rd.summarize_freshness(snapshot, now, rd.DEFAULT_SLOS)

    assert any(w["code"] == "chunks_missing" and w["severity"] == "critical" for w in warnings)


def test_db_unavailable_degrades_honestly():
    now = datetime(2026, 5, 22, tzinfo=timezone.utc)
    summary, warnings = rd.summarize_freshness(
        {"available": False, "error": "ConnectionRefusedError"},
        now,
        rd.DEFAULT_SLOS,
    )

    assert summary["status"] == "unavailable"
    assert warnings[0]["code"] == "db_unavailable"


@pytest.mark.asyncio
async def test_build_report_stable_json_without_db(monkeypatch, tmp_path):
    async def fake_snapshot(tenant_slug: str, since_hours: int = 24) -> dict:
        return {"available": False, "tenant_slug": tenant_slug, "error": "offline"}

    monkeypatch.setattr(rd, "collect_db_snapshot", fake_snapshot)
    monkeypatch.setattr(rd, "VALIDATION_DIR", tmp_path)
    monkeypatch.setattr(rd, "OUTPUT_DIR", tmp_path / "reliability")

    report = await rd.build_report("hypeproof-lab", write=False)

    assert report["schema_version"] == 1
    assert report["tenant_slug"] == "hypeproof-lab"
    assert report["status"] == "degraded"
    assert report["notification"]["event_type"] == "reliability.daily"
    assert report["notification"]["payload"]["warning_count"] >= 1


def test_distill_warns_when_events_have_no_decisions():
    now = datetime(2026, 5, 22, tzinfo=timezone.utc)
    snapshot = _snapshot(now)
    snapshot["distill"]["decisions_extracted"] = 0

    summary, warnings = rd.summarize_distill(snapshot)

    assert summary["status"] == "degraded"
    assert any(w["code"] == "distill_zero_decisions" for w in warnings)
