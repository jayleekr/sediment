"""End-to-end test for the FULL device-flow approval — CLI ↔ web UI ↔ backend.

Catches the bug class that broke 2026-05-22 deploy:
  - AuthBridge hard-coded a redirect to /sediment after sign-in,
    bouncing the user off /sediment/device before they could approve.
  - Our existing CLI Rust e2e_login.rs only used the dev-mode shortcut
    (DB UPDATE), so it never exercised the web approval page.

This test drives Playwright against the actual rendered page:
  1. CLI side: POST /oauth-device/start → user_code
  2. Inject a valid JWT into localStorage (bypasses NextAuth sign-in,
     which would otherwise need a real GitHub session — out of scope)
  3. Navigate to /sediment/device?user_code=XXX
  4. Assert: page does NOT redirect away (the AuthBridge regression)
  5. Assert: "Approve device" button is visible with user_code pre-filled
  6. Click Approve
  7. Backend: device row status should now be 'approved'
  8. CLI side: POST /oauth-device/poll → 200 with JWT

Runs against:
  - SEDIMENT_E2E_BASE_URL: API host (defaults http://localhost:10101)
  - SEDIMENT_E2E_WEB_URL:  Web host (defaults http://localhost:3000)
"""
from __future__ import annotations
import asyncio
import os

import httpx
import pytest
from sqlalchemy import text

from applications.sediment_platform.main import app
from lab_lib.db import service_session


pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_DB") == "1", reason="DB not available"
)


SEED_EMAIL = "jay.lee@sonatus.com"


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_device_flow_web_approval_end_to_end(monkeypatch):
    """Full human flow: CLI gets code → user visits /device → clicks
    Approve → CLI completes. Skips the GitHub OAuth step by injecting a
    JWT directly into localStorage (NextAuth callback is not under test;
    AuthBridge + the approve button are)."""
    # Requires Playwright + a live web URL + matching API URL.
    web_url = os.environ.get("SEDIMENT_E2E_WEB_URL")
    api_url = os.environ.get("SEDIMENT_E2E_API_URL")
    pre_minted = os.environ.get("SEDIMENT_E2E_JWT")  # for prod (no dev-token)
    if not (web_url and api_url):
        pytest.skip("set SEDIMENT_E2E_WEB_URL + SEDIMENT_E2E_API_URL")
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        pytest.skip("playwright not installed")

    api = httpx.AsyncClient(base_url=api_url, timeout=20.0)

    # 1. CLI starts device flow against the API the web talks to
    r = await api.post("/api/v1/auth/oauth-device/start",
                       json={"client_id": "sediment-cli"})
    assert r.status_code == 200, r.text
    body = r.json()
    user_code = body["user_code"]
    device_code = body["device_code"]

    # 2. Resolve a JWT the web page will accept. Two paths:
    #    a) SEDIMENT_E2E_JWT env override — for prod where /dev-token is gated.
    #    b) /dev-token endpoint — for local/staging.
    if pre_minted:
        token = pre_minted
    else:
        r = await api.post("/api/v1/auth/dev-token", json={"email": SEED_EMAIL})
        assert r.status_code == 200, f"dev-token failed (prod? set SEDIMENT_E2E_JWT): {r.text}"
        token = r.json()["token"]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        # Inject curator.token BEFORE the page loads so AuthBridge sees it
        # immediately on hydrate (no sign-in roundtrip).
        await context.add_init_script(
            f"window.localStorage.setItem('curator.token', '{token}');"
        )
        page = await context.new_page()

        # 3. Navigate to /sediment/device?user_code=XXX
        target_url = f"{web_url}/sediment/device?user_code={user_code}"
        await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
        # Give AuthBridge a moment to NOT redirect away.
        await page.wait_for_timeout(2000)

        # 4. Regression assertion: did we get bounced away from /device?
        current = page.url
        assert "/sediment/device" in current, (
            f"AuthBridge regression — user got redirected from /sediment/device "
            f"to {current} after sign-in. This is the bug from 2026-05-22."
        )

        # 5. Approve button visible with user_code pre-filled
        await page.wait_for_selector("button:has-text('Approve device')", timeout=15000)
        # User code should be in the input
        input_value = await page.input_value('input[placeholder="ABCD-EFGH"]')
        assert input_value == user_code, f"user_code mismatch: input={input_value} expected={user_code}"

        # 6. Click Approve
        await page.click("button:has-text('Approve device')")
        # Wait for the success screen
        await page.wait_for_selector("text=Device authorized", timeout=15000)

        await context.close()
        await browser.close()

    # 7. CLI poll → JWT minted (atomic CAS consumes the approved row).
    # We test the OBSERVABLE behaviour (poll returns a token) rather than
    # poking the DB directly, since against a remote API the DB isn't
    # reachable here. Wait past slow_down first.
    await asyncio.sleep(5.1)
    r = await api.post("/api/v1/auth/oauth-device/poll",
                       json={"device_code": device_code})
    assert r.status_code == 200, f"poll failed after web approval: {r.text}"
    minted = r.json()
    assert minted.get("token"), "no token in poll response"
    assert minted["member_id"], "no member_id"

    await api.aclose()


async def test_authbridge_does_not_redirect_from_deep_paths(monkeypatch):
    """Lighter regression test for the AuthBridge bug — no backend needed.
    Spins up Playwright against the prod URL, opens /sediment/library
    (any deep path), injects a token, asserts the URL stays put after
    AuthBridge runs. If this fails, deep links + sign-in are broken."""
    web_url = os.environ.get("SEDIMENT_E2E_WEB_URL")
    if not web_url:
        pytest.skip("set SEDIMENT_E2E_WEB_URL")
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        pytest.skip("playwright not installed")

    # Use a syntactically valid but unverifiable JWT — AuthBridge only
    # CALLS setToken and then redirects/reloads; it doesn't validate the
    # token. The library page WILL fail to fetch, but that's not the
    # property under test.
    fake_jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiJ0ZXN0Iiwib3JnX2lkIjoidGVzdCJ9."
        "x" * 43
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.add_init_script(
            f"window.localStorage.setItem('curator.token', '{fake_jwt}');"
        )
        page = await context.new_page()
        deep_url = f"{web_url}/sediment/library"
        await page.goto(deep_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)  # AuthBridge effect window
        assert "/sediment/library" in page.url, (
            f"AuthBridge bounced /sediment/library → {page.url} — "
            "deep-link sign-in is broken again."
        )
        await context.close()
        await browser.close()
