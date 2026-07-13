"""Dispatch a single rubric check.

Supported types:
  bash   — subprocess + exit code / stdout match
  sql    — SQL query via app or service role
  http   — HTTP request, expected status / json body
  python — import module.function, execute, expect CheckResult or boolean
  e2e    — delegated to e2e_runner
"""
from __future__ import annotations
import asyncio
import importlib
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any
import httpx
from sqlalchemy import text

from lab_lib.db import app_session, service_session
from lab_lib.logging import get_logger
from .types import CheckResult, Severity

log = get_logger("validator.dispatch")

# Rubric bash cmds use repo-root-relative paths. Validator runs from SVC_DIR,
# so we must set cwd explicitly. Anchor on init.sql to survive structure
# changes (2026-05-23 fix — parents[5] resolved to /Users/jaylee after the
# products/sediment → repo-root rename, breaking every bash check).
def _find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "infra" / "init.sql").is_file():
            return p
    env = os.environ.get("SEDIMENT_REPO_ROOT")
    return Path(env) if env else here.parents[3]


_REPO_ROOT = str(_find_repo_root())


async def dispatch(spec: dict) -> CheckResult:
    """Run a single check spec and return a CheckResult."""
    cid = spec["id"]
    title = spec["title"]
    layer = spec["layer"]
    severity: Severity = spec["severity"]
    typ = spec["type"]
    # Env-gated checks (rubric `enabled_env`): skip cleanly when the flag is
    # unset so PR/CI builds without an API key don't spuriously fail a blocker.
    enabled_env = spec.get("enabled_env")
    if enabled_env and os.environ.get(enabled_env, "").strip().lower() not in ("1", "true", "yes", "on"):
        return CheckResult(
            id=cid, title=title, layer=layer, severity=severity,
            passed=True, actual={"skipped": enabled_env},
            message=f"skipped: {enabled_env} not set",
        )
    t0 = time.time()
    try:
        if typ == "bash":
            r = _run_bash(spec)
        elif typ == "sql":
            r = await _run_sql(spec)
        elif typ == "http":
            r = await _run_http(spec)
        elif typ == "python":
            r = await _run_python(spec)
        elif typ == "e2e":
            from .e2e_runner import run_flow
            r = await run_flow(spec)
        else:
            r = CheckResult(id=cid, title=title, layer=layer, severity=severity,
                            passed=False, message=f"unknown check type: {typ}")
    except Exception as e:
        log.exception("dispatch.error", cid=cid)
        r = CheckResult(id=cid, title=title, layer=layer, severity=severity,
                        passed=False, message=f"exception: {type(e).__name__}: {e}")
    finally:
        if not isinstance(r, CheckResult):
            r = CheckResult(id=cid, title=title, layer=layer, severity=severity,
                            passed=False, message=f"check returned non-CheckResult: {type(r).__name__}")

    # fill in metadata
    r.id = cid
    r.title = title
    r.layer = layer
    r.severity = severity
    r.duration_ms = int((time.time() - t0) * 1000)
    return r


# ============================================================
# bash
# ============================================================

def _run_bash(spec: dict) -> CheckResult:
    cmd = spec["cmd"]
    expected_exit = spec.get("expected_exit", 0)
    contains = spec.get("expected_stdout_contains")
    regex = spec.get("expected_stdout_regex")
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60, cwd=_REPO_ROOT)
    ok = proc.returncode == expected_exit
    if ok and contains:
        ok = contains in proc.stdout
    if ok and regex:
        ok = re.search(regex, proc.stdout) is not None
    msg = ""
    if not ok:
        msg = (
            f"exit={proc.returncode} (expected {expected_exit})\n"
            f"stdout: {proc.stdout[:500]}\n"
            f"stderr: {proc.stderr[:500]}"
        )
    return CheckResult(
        id=spec["id"], title=spec["title"], layer=spec["layer"], severity=spec["severity"],
        passed=ok, actual={"exit": proc.returncode}, expected={"exit": expected_exit}, message=msg,
    )


# ============================================================
# SQL
# ============================================================

async def _run_sql(spec: dict) -> CheckResult:
    sql = spec["sql"]
    role = spec.get("role", "service")
    tenant_id = spec.get("tenant_id")
    expected_rows = spec.get("expected_rows")
    expected_min_rows = spec.get("expected_min_rows")
    expected_value = spec.get("expected_value")
    expected_min_value = spec.get("expected_min_value")

    if role == "app" and not tenant_id:
        return CheckResult(
            id=spec["id"], title=spec["title"], layer=spec["layer"], severity=spec["severity"],
            passed=False, message="app role check missing tenant_id",
        )

    async def execute():
        if role == "service":
            async with service_session() as s:
                r = await s.execute(text(sql))
                return r.fetchall()
        else:
            async with app_session(tenant_id) as s:
                r = await s.execute(text(sql))
                return r.fetchall()

    rows = await execute()

    ok = True
    actual: Any = {"rows": len(rows)}
    if expected_rows is not None:
        ok = ok and len(rows) == expected_rows
    if expected_min_rows is not None:
        ok = ok and len(rows) >= expected_min_rows
    if expected_value is not None:
        first_val = rows[0][0] if rows else None
        actual["value"] = first_val
        ok = ok and first_val == expected_value
    if expected_min_value is not None:
        first_val = rows[0][0] if rows else None
        actual["value"] = first_val
        ok = ok and (first_val is not None and first_val >= expected_min_value)

    return CheckResult(
        id=spec["id"], title=spec["title"], layer=spec["layer"], severity=spec["severity"],
        passed=ok, actual=actual,
        expected={k: v for k, v in spec.items() if k.startswith("expected_")},
        message="" if ok else f"sql mismatch: actual={actual}",
    )


# ============================================================
# HTTP
# ============================================================

async def _run_http(spec: dict) -> CheckResult:
    method = spec.get("method", "GET").upper()
    url = spec["url"]
    expected_status = spec.get("expected_status", 200)
    headers = spec.get("headers", {})
    json_body = spec.get("json")
    timeout = spec.get("timeout", 10)

    async with httpx.AsyncClient() as client:
        try:
            r = await client.request(method, url, headers=headers, json=json_body, timeout=timeout)
            ok = r.status_code == expected_status
            msg = "" if ok else f"status={r.status_code} expected={expected_status} body={r.text[:200]}"
            return CheckResult(
                id=spec["id"], title=spec["title"], layer=spec["layer"], severity=spec["severity"],
                passed=ok, actual={"status": r.status_code, "url": url},
                expected={"status": expected_status}, message=msg,
            )
        except httpx.ConnectError as e:
            return CheckResult(
                id=spec["id"], title=spec["title"], layer=spec["layer"], severity=spec["severity"],
                passed=False, actual={"error": "connection refused"},
                expected={"status": expected_status},
                message=f"service not reachable: {url} ({e})",
            )


# ============================================================
# Python
# ============================================================

async def _run_python(spec: dict) -> CheckResult:
    module_name = spec["module"]
    fn_name = spec["function"]
    params = spec.get("params", {})

    mod = importlib.import_module(module_name)
    fn = getattr(mod, fn_name)
    if asyncio.iscoroutinefunction(fn):
        result = await fn(spec=spec, **params)
    else:
        result = fn(spec=spec, **params)

    if isinstance(result, CheckResult):
        return result
    if isinstance(result, dict):
        return CheckResult(
            id=spec["id"], title=spec["title"], layer=spec["layer"], severity=spec["severity"],
            passed=bool(result.get("passed", False)),
            actual=result.get("actual"),
            expected=result.get("expected"),
            message=result.get("message", ""),
            artifacts=result.get("artifacts", []),
        )
    if isinstance(result, bool):
        return CheckResult(
            id=spec["id"], title=spec["title"], layer=spec["layer"], severity=spec["severity"],
            passed=result, message="" if result else f"{module_name}.{fn_name} returned False",
        )
    return CheckResult(
        id=spec["id"], title=spec["title"], layer=spec["layer"], severity=spec["severity"],
        passed=False,
        message=f"unexpected return type from {module_name}.{fn_name}: {type(result).__name__}",
    )
