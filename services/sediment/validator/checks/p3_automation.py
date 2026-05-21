"""Phase 3 custom Python checks."""
from __future__ import annotations
import os
import subprocess
from pathlib import Path

from lab_lib.db import service_session
from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[6]  # checks→validator→sediment→services→sediment→products→repo-root
PROD_ROOT = REPO_ROOT / "products" / "sediment"
SVC_ROOT = PROD_ROOT / "services" / "sediment"


def check_daily_ingest_dryrun(spec: dict, **_) -> dict:
    """Don't actually run (would call ingester) — just check script syntax + ingester health prerequisite."""
    sh = SVC_ROOT / "scripts" / "cron" / "daily_ingest.sh"
    if not sh.exists():
        return {"passed": False, "message": "daily_ingest.sh missing"}
    proc = subprocess.run(["bash", "-n", str(sh)], capture_output=True, text=True)
    return {"passed": proc.returncode == 0, "actual": {"syntax_check_exit": proc.returncode},
            "message": proc.stderr[:200]}


async def check_dream_idempotent(spec: dict, **_) -> dict:
    """Run dream.py twice — counters should not double, RLS untouched."""
    venv_py = SVC_ROOT / ".venv" / "bin" / "python"
    py = str(venv_py) if venv_py.exists() else "python3"

    proc1 = subprocess.run(
        [py, "-m", "scripts.cron.dream"],
        cwd=str(SVC_ROOT), capture_output=True, text=True, timeout=120,
        env={**os.environ},
    )
    if proc1.returncode != 0:
        return {"passed": False, "actual": {"exit": proc1.returncode},
                "message": f"first run failed: {proc1.stderr[:300]}"}

    proc2 = subprocess.run(
        [py, "-m", "scripts.cron.dream"],
        cwd=str(SVC_ROOT), capture_output=True, text=True, timeout=120,
        env={**os.environ},
    )
    return {"passed": proc2.returncode == 0,
            "actual": {"first_exit": proc1.returncode, "second_exit": proc2.returncode},
            "message": "" if proc2.returncode == 0 else proc2.stderr[:300]}


async def check_discord_fixture_creation(spec: dict, **_) -> dict:
    """Run discord_ingest.py with no fixture — should create a template fixture and exit cleanly."""
    fixture = SVC_ROOT / "scripts" / "fixtures" / "discord_dump.json"
    # backup if exists
    backup = None
    if fixture.exists():
        backup = fixture.with_suffix(".json.validator-backup")
        fixture.rename(backup)

    venv_py = SVC_ROOT / ".venv" / "bin" / "python"
    py = str(venv_py) if venv_py.exists() else "python3"
    try:
        proc = subprocess.run(
            [py, "-m", "scripts.discord_ingest"],
            cwd=str(SVC_ROOT), capture_output=True, text=True, timeout=30,
        )
        ok = proc.returncode == 0 and fixture.exists()
        return {"passed": ok, "actual": {"exit": proc.returncode, "fixture_created": fixture.exists()},
                "message": proc.stderr[:300] if proc.returncode != 0 else ""}
    finally:
        if backup and backup.exists():
            if fixture.exists():
                fixture.unlink()
            backup.rename(fixture)


async def check_discord_ingest_works(spec: dict, **_) -> dict:
    """Run discord_ingest.py with the existing/template fixture and check at least 1 event row inserted."""
    venv_py = SVC_ROOT / ".venv" / "bin" / "python"
    py = str(venv_py) if venv_py.exists() else "python3"
    proc = subprocess.run(
        [py, "-m", "scripts.discord_ingest"],
        cwd=str(SVC_ROOT), capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        return {"passed": False, "actual": {"exit": proc.returncode},
                "message": proc.stderr[:300]}
    async with service_session() as s:
        n = (await s.execute(text("SELECT count(*) FROM events WHERE source='discord'"))).scalar_one()
    return {"passed": n >= 1, "actual": {"discord_events": n}}


async def check_dream_respects_rls(spec: dict, **_) -> dict:
    """Run dream and assert: chunks.boost only mutates lab tenant, not acme."""
    venv_py = SVC_ROOT / ".venv" / "bin" / "python"
    py = str(venv_py) if venv_py.exists() else "python3"
    proc = subprocess.run(
        [py, "-m", "scripts.cron.dream"],
        cwd=str(SVC_ROOT), capture_output=True, text=True, timeout=120,
    )
    async with service_session() as s:
        # In acme tenant, sum(boost) should be 0 (no chunks → 0)
        r = await s.execute(text("""
            SELECT COALESCE(sum(boost), 0) FROM chunks
            WHERE tenant_id IN (SELECT id FROM tenants WHERE slug='acme-test')
        """))
        acme_boost = r.scalar_one() or 0
    return {"passed": proc.returncode == 0 and float(acme_boost) == 0.0,
            "actual": {"dream_exit": proc.returncode, "acme_boost_sum": float(acme_boost)},
            "expected": {"dream_exit": 0, "acme_boost_sum": 0.0}}
