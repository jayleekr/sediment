"""Authenticated production answer smoke for Sediment.

Creates a real conversation, opens the production deep link in Chromium, lets
the UI auto-stream the answer, then verifies the assistant answer and citations
remain visible after reload.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

API = os.environ.get("SEDIMENT_API", "https://hypeproof-sediment.fly.dev").rstrip("/")
WEB = os.environ.get("SEDIMENT_WEB_URL", "https://sediment.hypeproof-ai.xyz").rstrip("/")
TOKEN = os.environ.get("SEDIMENT_CI_TOKEN", "").strip()
QUERY = os.environ.get("SEDIMENT_SMOKE_QUERY", "라이언이 4월에 쓴 mirror-loop 칼럼")
KEEP_CONVERSATION = os.environ.get("SEDIMENT_SMOKE_KEEP_CONVERSATION") == "1"
OUT_JSON = os.environ.get("SEDIMENT_SMOKE_JSON_OUT")
REPO_ROOT = Path(__file__).resolve().parents[4]
SCREENSHOT_DIR = Path(
    os.environ.get(
        "SEDIMENT_SMOKE_SCREENSHOT_DIR",
        str(REPO_ROOT / "output" / "validation" / "screenshots" / "prod-answer-smoke"),
    )
)


def _headers() -> dict[str, str]:
    if not TOKEN:
        raise RuntimeError("SEDIMENT_CI_TOKEN is required")
    _validate_human_token(TOKEN)
    return {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def _validate_human_token(token: str) -> None:
    """Fail early when the shared CI token is a service JWT.

    /api/v1/conversations writes identity.member_id into conversations.user_id,
    which is a UUID FK. Service tokens intentionally use sub=service:<caller>,
    so they are valid for read-only recall but invalid for answer smoke setup.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception as exc:
        raise RuntimeError(f"SEDIMENT_CI_TOKEN is not a JWT: {exc}") from exc
    if claims.get("role") == "service" or str(claims.get("sub", "")).startswith("service:"):
        raise RuntimeError("SEDIMENT_CI_TOKEN must be a human member JWT for prod answer smoke")


async def _create_conversation() -> str:
    title = f"test: prod-answer-e2e {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}"
    async with httpx.AsyncClient(timeout=20) as client:
        cr = await client.post(
            f"{API}/api/v1/conversations",
            headers=_headers(),
            json={"title": title},
        )
        cr.raise_for_status()
        conv_id = cr.json()["id"]
        mr = await client.post(
            f"{API}/api/v1/conversations/{conv_id}/messages",
            headers=_headers(),
            json={"content": QUERY, "role": "user"},
        )
        mr.raise_for_status()
        return conv_id


async def _delete_conversation(conv_id: str) -> None:
    async with httpx.AsyncClient(timeout=20) as client:
        await client.delete(f"{API}/api/v1/conversations/{conv_id}", headers=_headers())


async def _api_messages(conv_id: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{API}/api/v1/conversations/{conv_id}", headers=_headers())
        r.raise_for_status()
        return r.json().get("messages", [])


async def _verify_browser(conv_id: str) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    url = f"{WEB}/sediment/c/{conv_id}?ask=1"
    reload_url = f"{WEB}/sediment/c/{conv_id}"
    screenshot = SCREENSHOT_DIR / f"{conv_id}.png"
    reload_screenshot = SCREENSHOT_DIR / f"{conv_id}-reload.png"
    console_errors: list[str] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        await context.add_init_script(
            f"localStorage.setItem('curator.token', {json.dumps(TOKEN)});"
        )
        page = await context.new_page()
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_selector("[data-role='assistant']", timeout=90_000)
        await page.wait_for_function(
            """() => {
              const alert = document.querySelector('[role="alert"]');
              if (alert && /Answer generation failed|Conversation could not be loaded/i.test(alert.textContent || '')) {
                return true;
              }
              const assistants = [...document.querySelectorAll('[data-role="assistant"]')];
              const last = assistants.at(-1);
              const text = last?.textContent || '';
              return text.length > 40 && !text.includes('thinking');
            }""",
            timeout=90_000,
        )
        await page.screenshot(path=str(screenshot), full_page=True)

        body = await page.text_content("body") or ""
        alert_text = await page.locator("[role='alert']").all_text_contents()
        assistant_count = await page.locator("[data-role='assistant']").count()
        citation_card_count = await page.locator("aside li").count()
        failed = any("Answer generation failed" in t for t in alert_text)

        if failed:
            raise AssertionError(f"answer generation alert visible: {alert_text}")
        if assistant_count < 1:
            raise AssertionError("assistant bubble is not visible")
        if citation_card_count < 1 or "citation" not in body:
            raise AssertionError("citations are not visible")

        await page.goto(reload_url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_selector("[data-role='assistant']", timeout=30_000)
        await page.screenshot(path=str(reload_screenshot), full_page=True)
        reload_body = await page.text_content("body") or ""
        if "citation" not in reload_body:
            raise AssertionError("persisted reload lost citation UI")

        await browser.close()

    messages = await _api_messages(conv_id)
    assistant_messages = [m for m in messages if m.get("role") == "assistant"]
    return {
        "url": url,
        "reload_url": reload_url,
        "assistant_count": assistant_count,
        "assistant_messages": len(assistant_messages),
        "citation_card_count": citation_card_count,
        "screenshots": [str(screenshot), str(reload_screenshot)],
        "console_errors": console_errors[:20],
    }


async def main() -> None:
    conv_id = ""
    passed = False
    result: dict[str, Any] = {"api": API, "web": WEB, "query": QUERY}
    try:
        conv_id = await _create_conversation()
        result["conv_id"] = conv_id
        result.update(await _verify_browser(conv_id))
        passed = True
        result["passed"] = True
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        result["passed"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(1)
    finally:
        if OUT_JSON:
            Path(OUT_JSON).write_text(json.dumps(result, ensure_ascii=False, indent=2))
        if passed and conv_id and not KEEP_CONVERSATION:
            await _delete_conversation(conv_id)


if __name__ == "__main__":
    asyncio.run(main())
