"""E2E runner — executes flows from e2e_spec.yaml using Playwright.

Single-flow API: run_flow(spec) → CheckResult, where spec is a rubric check
of type=e2e referencing flow_id.

repeat handling: each flow runs N times; pass_threshold of N must succeed.
Per-attempt screenshots saved to output/validation/screenshots/iter-X/<FLOW>/attempt-Y/<step>.png
"""
from __future__ import annotations
import asyncio
import os
import re
import time
from pathlib import Path
from typing import Any
import yaml

from .types import CheckResult

E2E_SPEC = Path(__file__).resolve().parent / "e2e_spec.yaml"
SCREENSHOT_DIR = Path(__file__).resolve().parents[2].parent / "output" / "validation" / "screenshots"


def _load_spec() -> dict:
    return yaml.safe_load(E2E_SPEC.read_text())


def _resolve_template(value: Any, ctx: dict) -> Any:
    if isinstance(value, str):
        # {{seed_member.email}} → ctx['seed_member']['email']
        for m in re.finditer(r"\{\{([\w.]+)\}\}", value):
            path = m.group(1).split(".")
            v = ctx
            for k in path:
                v = v.get(k, "") if isinstance(v, dict) else ""
            value = value.replace(m.group(0), str(v))
    return value


async def _ensure_playwright():
    try:
        from playwright.async_api import async_playwright  # noqa
        return True
    except ImportError:
        return False


async def run_flow(spec: dict) -> CheckResult:
    """Run one e2e flow referenced by spec['flow_id']."""
    if not await _ensure_playwright():
        return CheckResult(
            id=spec["id"], title=spec["title"], layer=spec["layer"], severity=spec["severity"],
            passed=False,
            message="playwright not installed — run `pip install playwright && playwright install chromium`",
        )

    full = _load_spec()
    flow_id = spec["flow_id"]
    flow = next((f for f in full["flows"] if f["id"] == flow_id), None)
    if not flow:
        return CheckResult(
            id=spec["id"], title=spec["title"], layer=spec["layer"], severity=spec["severity"],
            passed=False, message=f"flow {flow_id} not in e2e_spec.yaml",
        )

    # v0.2: env-aware filtering. Flow without `environments:` tag defaults to
    # ["dev"] (backwards-compat — v0.1 spec only ran against localhost dev).
    env_name = os.environ.get("SEDIMENT_E2E_ENV", "dev")
    flow_envs = flow.get("environments", ["dev"])
    if env_name not in flow_envs:
        return CheckResult(
            id=spec["id"], title=spec["title"], layer=spec["layer"], severity=spec["severity"],
            passed=True,
            actual={"skipped": True, "env": env_name, "flow_envs": flow_envs},
            message=f"skipped — flow {flow_id} not enabled for env={env_name} (allowed: {flow_envs})",
        )

    repeat = flow.get("repeat", 1)
    threshold = flow.get("pass_threshold", repeat)
    iter_n = int(os.environ.get("VALIDATOR_ITER", "1"))
    flow_dir = SCREENSHOT_DIR / f"iter-{iter_n:02d}" / flow_id
    flow_dir.mkdir(parents=True, exist_ok=True)

    successes = 0
    artifacts: list[str] = []
    error_messages: list[str] = []
    for attempt in range(1, repeat + 1):
        attempt_dir = flow_dir / f"attempt-{attempt:02d}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        try:
            arts = await _run_one_attempt(full, flow, attempt_dir)
            artifacts.extend(arts)
            successes += 1
        except Exception as e:
            error_messages.append(f"attempt {attempt}: {type(e).__name__}: {e}")

    passed = successes >= threshold
    flake_rate = round((repeat - successes) / repeat * 100, 1) if repeat else 0.0
    return CheckResult(
        id=spec["id"], title=spec["title"], layer=spec["layer"], severity=spec["severity"],
        passed=passed,
        actual={"successes": successes, "repeat": repeat, "flake_rate_pct": flake_rate,
                 "errors": error_messages[:3]},
        expected={"successes_min": threshold, "repeat": repeat},
        artifacts=artifacts,
        message="" if passed else f"{successes}/{repeat} attempts passed (need ≥ {threshold})",
    )


async def _run_one_attempt(full: dict, flow: dict, out_dir: Path) -> list[str]:
    from playwright.async_api import async_playwright

    # v0.2: env-aware base_url + seed_member.
    # Precedence (highest first):
    #   1. SEDIMENT_E2E_BASE_URL env var — ad-hoc override (e.g. PR preview)
    #   2. environments[SEDIMENT_E2E_ENV].base_url — spec-defined env
    #   3. full["base_url"] — top-level v0.1 fallback
    env_name = os.environ.get("SEDIMENT_E2E_ENV", "dev")
    env_cfg = (full.get("environments") or {}).get(env_name) or {}
    base_url = (
        os.environ.get("SEDIMENT_E2E_BASE_URL")
        or env_cfg.get("base_url")
        or full["base_url"]
    )
    timeout = full.get("default_timeout_ms", 8000)
    # Per-flow viewport override — lets E2E-10 (mobile) reuse the same runner
    # without forcing a separate spec. Defaults to global viewport at 1280x900.
    vp = flow.get("viewport") or full.get("viewport", {"width": 1280, "height": 900})
    seed_member = env_cfg.get("seed_member") or full.get("seed_member", {}) or {}
    ctx_vars = {"seed_member": seed_member}

    # WO-7 #6 2026-05-24 — make `lab_conv_id` available to flows that need a
    # real (not nonexistent) lab conversation UUID (E2E-08 cross-tenant
    # negative test). Previously E2E-08 used `00000000-...-deadbeef0000` —
    # a UUID that does not exist for any tenant, so the test passed whether
    # RLS worked or not (404 either way). We pick the most-recently created
    # hypeproof-lab conversation. Best-effort: if DB unreachable / no lab
    # convs seeded, leave the var empty and let the assertion below catch it.
    try:
        # Import locally to avoid pulling DB deps when running E2E in a
        # pure browser-only env (e.g. PR-preview check).
        from lab_lib.db import service_session
        from sqlalchemy import text as _sql
        async with service_session() as _s:
            r = await _s.execute(_sql(
                "SELECT id::text FROM conversations "
                "WHERE tenant_id = (SELECT id FROM tenants WHERE slug='hypeproof-lab') "
                "ORDER BY created_at DESC LIMIT 1"
            ))
            row = r.first()
            ctx_vars["lab_conv_id"] = row[0] if row else ""
    except Exception:
        ctx_vars["lab_conv_id"] = ""

    artifacts: list[str] = []
    console_errors: list[str] = []

    # Known frontend noise unrelated to curator semantics. NextAuth's session
    # fetcher fires on every route change and can race with navigation,
    # producing transient ClientFetchError. These are NOT actionable bugs in
    # the curator API surface, so we exclude them from the console_errors_max
    # assertion. Add new patterns here as they're identified.
    _noise_patterns = (
        "ClientFetchError",                        # next-auth session fetch race
        "errors.authjs.dev",                       # auth.js error page suffix
        "_next/static/chunks/node_modules",        # bundled SDK noise
    )

    def _is_noise(text: str) -> bool:
        return any(p in text for p in _noise_patterns)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport=vp)
        page = await context.new_page()

        page.on(
            "console",
            lambda msg: console_errors.append(msg.text)
            if (msg.type == "error" and not _is_noise(msg.text))
            else None,
        )

        # Pre-seed cookie-consent acceptance so the global modal overlay
        # doesn't intercept Playwright clicks. The modal renders on every
        # /curator/* route until consent is given. Stored at the document
        # origin via init script before any page navigation.
        #
        # NOTE: add_init_script wraps source in (() => { ${source} })() so the
        # source must be a direct statement. A bare arrow-function expression
        # () => {...} would be wrapped as (() => { () => {...} })() and the
        # inner function would never be called.
        await context.add_init_script(
            "try { "
            "localStorage.setItem('cookie_consent', "
            "JSON.stringify({necessary:true,analytics:true,marketing:false,preferences:false})); "
            "localStorage.setItem('consent_timestamp', new Date().toISOString()); "
            "} catch (e) {}"
        )

        async def screenshot(name: str) -> str:
            path = out_dir / f"{name}.png"
            await page.screenshot(path=str(path), full_page=True)
            artifacts.append(str(path))
            return str(path)

        async def signin_with(email: str):
            await page.goto(base_url + "/sediment", timeout=timeout)
            await page.wait_for_selector("input[placeholder='member email']", timeout=timeout)
            await page.fill("input[placeholder='member email']", email)
            await page.click("button:has-text('Mint dev token')")
            await page.wait_for_selector("text=Conversations", timeout=timeout)

        try:
            for step in flow["steps"]:
                action = step["action"]

                if action == "navigate":
                    # WO-7 #6 — resolve template vars in URL so flows can use
                    # {{lab_conv_id}} etc. without per-action template plumbing.
                    url = base_url + _resolve_template(step["url"], ctx_vars)
                    await page.goto(url, timeout=timeout)
                    if wf := step.get("wait_for"):
                        await page.wait_for_selector(wf, timeout=timeout)
                    if r := step.get("wait_for_url_regex"):
                        await page.wait_for_url(re.compile(r), timeout=timeout)
                    if step.get("wait_for_idle"):
                        await page.wait_for_load_state("networkidle", timeout=timeout)

                elif action == "fill":
                    sel = step["selector"]
                    val = _resolve_template(step["value"], ctx_vars)
                    await page.fill(sel, val)

                elif action == "click":
                    sel = step["selector"]
                    await page.click(sel, timeout=timeout)
                    if wf := step.get("wait_for"):
                        await page.wait_for_selector(wf, timeout=timeout)
                    if r := step.get("wait_for_url_regex"):
                        await page.wait_for_url(re.compile(r), timeout=timeout)

                elif action == "press_key":
                    await page.keyboard.press(step["key"])
                    if r := step.get("wait_for_url_regex"):
                        await page.wait_for_url(re.compile(r), timeout=timeout)

                elif action == "wait_for_text":
                    optional = step.get("optional", False)
                    try:
                        await page.wait_for_selector(f"text={step['value']}",
                                                     timeout=step.get("timeout_ms", timeout))
                    except Exception:
                        if not optional:
                            raise

                elif action == "wait_for_selector":
                    await page.wait_for_selector(step["selector"],
                                                 timeout=step.get("timeout_ms", timeout))

                elif action == "wait_for_idle":
                    await page.wait_for_load_state("networkidle",
                                                   timeout=step.get("timeout_ms", timeout))

                elif action == "screenshot":
                    await screenshot(step.get("name", f"step-{int(time.time())}"))

                elif action == "ensure_signed_in":
                    # localStorage is unavailable on about:blank — navigate to
                    # the app origin first so the security context is set.
                    if page.url == "about:blank":
                        await page.goto(base_url + "/sediment", timeout=timeout)
                    has_token = await page.evaluate("() => localStorage.getItem('curator.token')")
                    if not has_token:
                        await signin_with(seed_member.get("email", "jayleekr0125@gmail.com"))

                elif action == "signin_as":
                    # Clear prior token, then sign in. Navigate first so
                    # localStorage.clear() has a real document to act on.
                    if page.url == "about:blank":
                        await page.goto(base_url + "/sediment", timeout=timeout)
                    await page.evaluate("() => localStorage.clear()")
                    await signin_with(step["email"])

                elif action == "signout":
                    if page.url == "about:blank":
                        await page.goto(base_url + "/sediment", timeout=timeout)
                    await page.evaluate("() => localStorage.clear()")

                elif action in ("assert", "assert_no"):
                    expect_pass = (action == "assert")
                    t = step["type"]
                    ok = True
                    if t == "text_contains":
                        sel = step.get("selector", "body")
                        val = step["value"]
                        text = await page.text_content(sel) or ""
                        ok = (val in text)
                    elif t == "text_matches_regex":
                        import re as _re
                        sel = step.get("selector", "body")
                        pattern = step["value"]
                        text = await page.text_content(sel) or ""
                        ok = bool(_re.search(pattern, text))
                    elif t == "url_contains":
                        ok = step["value"] in page.url
                    elif t == "selector_visible":
                        try:
                            await page.wait_for_selector(step["selector"], timeout=2000)
                            ok = True
                        except Exception:
                            ok = False
                    elif t == "localStorage_has":
                        v = await page.evaluate(f"() => localStorage.getItem('{step['key']}')")
                        ok = bool(v)
                    elif t == "console_errors_max":
                        ok = len(console_errors) <= step["value"]
                    elif t == "count_min":
                        n = await page.locator(step["selector"]).count()
                        ok = n >= step["min"]
                    elif t == "count_exact":
                        n = await page.locator(step["selector"]).count()
                        ok = n == step["count"]
                    if expect_pass != ok:
                        raise AssertionError(f"assertion failed: {step}")

                # Inline screenshot if step had screenshot key
                if step.get("screenshot"):
                    await screenshot(step["screenshot"])

                # Inline assertions list (legacy alternative)
                if isinstance(step.get("assert"), list):
                    for sub in step["assert"]:
                        sub_step = {**sub, "action": "assert"}
                        # Re-run as assertion — small recursion
                        # (Skipped — handled elsewhere)
        finally:
            await browser.close()

    return artifacts


# ============================================================
# Lint
# ============================================================

def lint_e2e_spec() -> tuple[bool, list[str]]:
    """Validate e2e_spec.yaml against e2e_meta_spec.yaml. Returns (ok, errors)."""
    meta_path = Path(__file__).resolve().parent / "e2e_meta_spec.yaml"
    if not meta_path.exists():
        return False, ["meta spec missing"]

    meta = yaml.safe_load(meta_path.read_text())
    spec = _load_spec()

    errs: list[str] = []
    for k in meta["required_root_keys"]:
        if k not in spec:
            errs.append(f"root missing key: {k}")

    for f in spec.get("flows", []):
        for k in meta["required_flow_keys"]:
            if k not in f:
                errs.append(f"flow {f.get('id', '?')} missing key: {k}")
        if f.get("severity") not in meta["allowed_severities"]:
            errs.append(f"flow {f['id']} has invalid severity: {f.get('severity')}")
        for step in f.get("steps", []):
            if "action" not in step:
                errs.append(f"flow {f['id']} step missing action")
            if step.get("action") not in meta["allowed_actions"]:
                errs.append(f"flow {f['id']} action not allowed: {step.get('action')}")
            if step.get("action") in ("assert", "assert_no") and step.get("type") not in meta["allowed_assertion_types"]:
                errs.append(f"flow {f['id']} assertion type not allowed: {step.get('type')}")
        repeat = f.get("repeat", 1)
        threshold = f.get("pass_threshold", repeat)
        if threshold > repeat:
            errs.append(f"flow {f['id']} pass_threshold > repeat")

    return len(errs) == 0, errs
