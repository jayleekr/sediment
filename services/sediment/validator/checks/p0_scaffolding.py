"""Phase 0 custom Python checks (those not expressible in YAML)."""
from __future__ import annotations
import re
from pathlib import Path
from sqlalchemy import text

from lab_lib.db import service_session
from ..types import CheckResult

# Anchor on infra/init.sql instead of parents[N] — survives layout changes
# (2026-05-23 fix: parents[6] resolved 2 above repo root after products/sediment
# → repo-root rename, making PROD_ROOT a non-existent path).
def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "infra" / "init.sql").is_file():
            return p
    return here.parents[4]


REPO_ROOT = _repo_root()
PROD_ROOT = REPO_ROOT  # Legacy alias — products/sediment/ no longer exists as a separate dir.

TENANT_TABLES = [
    "tenants", "subscriptions", "integrations", "members",
    "artifacts", "chunks", "conversations", "messages",
    "decisions", "actions", "events", "usage_events",
    "usage_daily", "audit_log",
]


async def check_all_tables_exist(spec: dict, **_) -> dict:
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT tablename FROM pg_tables
            WHERE schemaname='public' AND tablename = ANY(:names)
        """), {"names": TENANT_TABLES})
        found = {row[0] for row in r}
    missing = sorted(set(TENANT_TABLES) - found)
    return {
        "passed": len(missing) == 0,
        "actual": {"found": len(found), "missing": missing},
        "expected": {"required": len(TENANT_TABLES)},
        "message": f"missing tables: {missing}" if missing else "",
    }


async def check_rls_enabled_all(spec: dict, **_) -> dict:
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT tablename FROM pg_tables
            WHERE schemaname='public' AND tablename = ANY(:names) AND rowsecurity=false
        """), {"names": TENANT_TABLES})
        no_rls = [row[0] for row in r]
    return {
        "passed": len(no_rls) == 0,
        "actual": {"missing_rls": no_rls},
        "expected": "rowsecurity=true on all tenant tables",
        "message": f"RLS not enabled on: {no_rls}" if no_rls else "",
    }


async def check_force_rls_all(spec: dict, **_) -> dict:
    async with service_session() as s:
        # Newer PG: pg_class.relrowsecurity + relforcerowsecurity
        r = await s.execute(text("""
            SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname='public' AND c.relname = ANY(:names) AND c.relforcerowsecurity=false
        """), {"names": TENANT_TABLES})
        no_force = [row[0] for row in r]
    return {
        "passed": len(no_force) == 0,
        "actual": {"missing_force": no_force},
        "expected": "FORCE ROW LEVEL SECURITY on all",
        "message": f"FORCE RLS missing on: {no_force}" if no_force else "",
    }


def check_decisions_md_complete(spec: dict, **_) -> dict:
    p = PROD_ROOT / "DECISIONS.md"
    if not p.exists():
        return {"passed": False, "actual": "missing", "message": f"{p} not found"}
    content = p.read_text(encoding="utf-8")
    found = re.findall(r"^\|\s*(\d+)\s*\|", content, re.MULTILINE)
    answered = sorted(set(int(x) for x in found))
    expected = list(range(1, 21))
    missing = [n for n in expected if n not in answered]
    return {
        "passed": len(missing) == 0,
        "actual": {"answered_count": len(answered), "missing": missing},
        "expected": "20 numbered answers (1..20)",
        "message": f"missing answer numbers: {missing}" if missing else "",
    }


def check_env_example_complete(spec: dict, **_) -> dict:
    p = PROD_ROOT / ".env.example"
    if not p.exists():
        return {"passed": False, "actual": "missing", "message": ".env.example not found"}
    content = p.read_text()
    required = [
        "DATABASE_URL_APP", "DATABASE_URL_SERVICE", "REDIS_URL",
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
        "EMBEDDING_MODEL", "LLM_MODEL_DEFAULT",
        "JWT_SECRET", "JWT_ISSUER", "JWT_AUDIENCE",
        "SEDIMENT_PLATFORM_PORT", "SEDIMENT_LANGGRAPH_PORT",
        "VAULT_INGESTER_PORT", "METADATA_SVC_PORT",
        "DEFAULT_TENANT_SLUG", "DEFAULT_TENANT_NAME",
    ]
    missing = [k for k in required if k not in content]
    return {
        "passed": len(missing) == 0,
        "actual": {"missing": missing},
        "expected": f"{len(required)} required env vars",
        "message": f"missing env vars: {missing}" if missing else "",
    }


def check_makefile_targets(spec: dict, **_) -> dict:
    p = PROD_ROOT / "Makefile"
    if not p.exists():
        return {"passed": False, "message": "Makefile missing"}
    content = p.read_text()
    required = ["up:", "down:", "install:", "seed:", "ingest:", "verify-rls:",
                "test:", "platform:", "langgraph:", "ingester:", "metadata:"]
    missing = [t for t in required if t not in content]
    return {
        "passed": len(missing) == 0,
        "actual": {"missing": missing},
        "expected": "all essential make targets",
        "message": f"missing targets: {missing}" if missing else "",
    }
