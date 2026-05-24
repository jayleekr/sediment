"""Auto-fix recipes — applies declarative fix steps for known check failures.

Returns the number of fixes successfully applied (so the loop knows whether
the next iteration has any chance of progressing).

Code-level bugs (RAG quality, RLS leak, security) write a work-order.json
instead of attempting an automatic fix.
"""
from __future__ import annotations
import asyncio
import json
import os
import re
import subprocess
import time
from pathlib import Path
import httpx
import yaml

from lab_lib.logging import get_logger
from .types import PhaseReport, CheckResult

log = get_logger("validator.fixer")
RECIPES_PATH = Path(__file__).resolve().parent / "recipes.yaml"


def _load_recipes() -> dict:
    return yaml.safe_load(RECIPES_PATH.read_text())


def _matches(rec_match: dict, r: CheckResult) -> bool:
    if cid := rec_match.get("check_id"):
        if r.id != cid:
            return False
    if prefix := rec_match.get("check_id_prefix"):
        if not r.id.startswith(prefix):
            return False
    if items := rec_match.get("check_id_in"):
        if r.id not in items:
            return False
    if needle := rec_match.get("message_contains"):
        if needle not in (r.message or ""):
            return False
    return True


def _is_no_auto_fix(rid: str, no_list: list[str]) -> bool:
    for pattern in no_list:
        rx = pattern.replace("*", ".*")
        if re.match(f"^{rx}$", rid):
            return True
    return False


async def _wait_for_url(url: str, timeout: int = 30) -> bool:
    deadline = time.time() + timeout
    async with httpx.AsyncClient() as client:
        while time.time() < deadline:
            try:
                r = await client.get(url, timeout=2)
                if r.status_code == 200:
                    return True
            except Exception:
                pass
            await asyncio.sleep(1)
    return False


async def _apply_step(step: dict, repo_root: Path) -> bool:
    if cmd := step.get("cmd"):
        log.info("fixer.cmd", cmd=cmd)
        proc = subprocess.run(cmd, shell=True, cwd=str(repo_root),
                              capture_output=True, text=True,
                              timeout=step.get("timeout", 120))
        ok = proc.returncode == 0
        if not ok:
            log.warning("fixer.cmd.failed", cmd=cmd, stderr=proc.stderr[:200])
        if wait_url := step.get("wait_until_healthy"):
            # wait_until_healthy is a bash command (e.g., pg_isready) — re-run until exit 0
            deadline = time.time() + step.get("timeout", 30)
            while time.time() < deadline:
                p2 = subprocess.run(wait_url, shell=True, capture_output=True)
                if p2.returncode == 0:
                    return True
                await asyncio.sleep(1)
            return False
        return ok
    elif bg := step.get("background_cmd"):
        log.info("fixer.background", cmd=bg)
        # Spawn detached
        subprocess.Popen(bg, shell=True, cwd=str(repo_root),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        if wait_url := step.get("wait_for_url"):
            return await _wait_for_url(wait_url, step.get("timeout", 30))
        return True
    return False


def _collect_no_auto_patterns(recipes_doc: dict) -> list[str]:
    """Aggregate patterns that MUST NOT auto-fix.

    Reads the 4-tier policy explicitly:
      - Tier 3 ``human_required`` (RLS — instant release block)
      - Tier 4 ``forbid_ai_edit`` (init.sql / .env / billing.py / credentials*)
      - Legacy ``no_auto_fix`` (backwards-compat alias kept for old callers)

    2026-05-23 fix — previously only the legacy alias was read, so deleting
    it would have silently re-enabled auto-fix on Tier-3/4 patterns. RLS
    protection survived only because the alias coincidentally still listed
    ``P*-RLS-*``. Now each tier is read explicitly.
    """
    patterns: list[str] = []
    for key in ("human_required", "forbid_ai_edit", "no_auto_fix"):
        block = recipes_doc.get(key, [])
        if isinstance(block, dict):
            block = block.get("patterns", [])
        if isinstance(block, list):
            patterns.extend(p for p in block if isinstance(p, str))
    # Dedupe while preserving order — easier to read in logs.
    seen: set[str] = set()
    out: list[str] = []
    for p in patterns:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


async def fix_failures(report: PhaseReport, repo_root: Path,
                       output_dir: Path) -> dict:
    """Try to auto-fix every failure. Returns summary dict."""
    recipes_doc = _load_recipes()
    recipes = recipes_doc.get("recipes", [])
    no_auto = _collect_no_auto_patterns(recipes_doc)

    summary = {
        "total_failures": 0,
        "auto_fixable": 0,
        "auto_fixed": 0,
        "work_orders_written": 0,
        "actions": [],
    }

    failures = report.failures
    summary["total_failures"] = len(failures)
    if not failures:
        return summary

    work_orders: list[dict] = []
    for f in failures:
        if _is_no_auto_fix(f.id, no_auto):
            wo = {
                "check_id": f.id,
                "title": f.title,
                "layer": f.layer,
                "severity": f.severity,
                "actual": f.actual,
                "expected": f.expected,
                "message": f.message,
                "guidance": "Code/data-level fix required. Refer to TEST_REQUIREMENTS.md for the relevant L-layer.",
            }
            work_orders.append(wo)
            continue

        recipe = next((r for r in recipes if _matches(r["matches"], f)), None)
        if not recipe:
            work_orders.append({
                "check_id": f.id, "title": f.title, "layer": f.layer,
                "severity": f.severity, "message": f.message,
                "guidance": "No auto-fix recipe matches this failure pattern.",
            })
            continue

        summary["auto_fixable"] += 1
        all_ok = True
        for step in recipe["fix"]:
            ok = await _apply_step(step, repo_root)
            if not ok:
                all_ok = False
                break
        if all_ok:
            summary["auto_fixed"] += 1
            summary["actions"].append({"check_id": f.id, "recipe": recipe["description"]})
        else:
            work_orders.append({
                "check_id": f.id, "title": f.title, "message": f.message,
                "guidance": f"Auto-fix recipe '{recipe['description']}' failed mid-execution.",
            })

    if work_orders:
        wo_path = output_dir / "work-order.json"
        wo_path.write_text(json.dumps(work_orders, default=str, indent=2, ensure_ascii=False))
        summary["work_orders_written"] = len(work_orders)
        summary["work_order_path"] = str(wo_path)

    return summary
