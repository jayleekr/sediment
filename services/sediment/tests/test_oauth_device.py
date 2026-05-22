"""Tests for RFC 8628 device authorization grant.

Uses httpx.AsyncClient + ASGI transport so requests share the test's
event loop (avoids SQLAlchemy async pool cross-loop errors that bite
when using TestClient with a stack that hits real Postgres).

Requires the local Docker Postgres + a seeded member; skipped under
SKIP_DB=1.
"""
from __future__ import annotations
import asyncio
import os

import pytest
import httpx
from sqlalchemy import text

from applications.sediment_platform.main import app
from lab_lib.db import service_session


pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_DB") == "1", reason="DB not available"
)


SEED_EMAIL = "jay.lee@sonatus.com"


# ---------- fixtures ----------

@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                  base_url="http://test") as c:
        yield c


@pytest.fixture
async def seeded_email():
    async with service_session() as s:
        r = await s.execute(
            text("SELECT 1 FROM members WHERE lower(email) = lower(:e)"),
            {"e": SEED_EMAIL},
        )
        if r.first() is None:
            pytest.skip(f"member {SEED_EMAIL} not seeded — run `make seed`")
    return SEED_EMAIL


# ---------- tests ----------

async def test_start_returns_codes_and_uris(client):
    r = await client.post("/api/v1/auth/oauth-device/start",
                          json={"client_id": "sediment-cli"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "device_code" in body and len(body["device_code"]) >= 32
    assert "user_code" in body and len(body["user_code"]) == 9
    assert body["user_code"][4] == "-"
    assert body["expires_in"] == 600
    assert body["interval"] == 5
    assert body["verification_uri_complete"].endswith(
        f"user_code={body['user_code']}"
    )


async def test_poll_pending_returns_authorization_pending(client):
    r = await client.post("/api/v1/auth/oauth-device/start", json={})
    dc = r.json()["device_code"]
    r2 = await client.post("/api/v1/auth/oauth-device/poll",
                           json={"device_code": dc})
    assert r2.status_code == 400
    err = r2.json().get("detail", r2.json())
    assert err == {"error": "authorization_pending"}


async def test_poll_slow_down_when_polled_too_fast(client):
    r = await client.post("/api/v1/auth/oauth-device/start", json={})
    dc = r.json()["device_code"]
    await client.post("/api/v1/auth/oauth-device/poll",
                      json={"device_code": dc})
    r2 = await client.post("/api/v1/auth/oauth-device/poll",
                           json={"device_code": dc})
    assert r2.status_code == 400
    err = r2.json().get("detail", r2.json())
    assert err == {"error": "slow_down"}


async def test_poll_unknown_device_code_treated_as_expired(client):
    r = await client.post("/api/v1/auth/oauth-device/poll",
                          json={"device_code": "x" * 40})
    assert r.status_code == 400
    err = r.json().get("detail", r.json())
    assert err == {"error": "expired_token"}


async def test_approve_dev_disabled_without_flag(client, monkeypatch, seeded_email):
    r = await client.post("/api/v1/auth/oauth-device/start", json={})
    uc = r.json()["user_code"]
    monkeypatch.delenv("SEDIMENT_DEV_MODE", raising=False)
    r2 = await client.post("/api/v1/auth/oauth-device/approve-dev",
                           json={"user_code": uc, "email": seeded_email})
    assert r2.status_code == 403


async def test_full_device_flow_via_approve_dev(client, monkeypatch, seeded_email):
    """End-to-end RFC 8628 flow using the dev-mode approval shortcut."""
    monkeypatch.setenv("SEDIMENT_DEV_MODE", "1")

    r = await client.post("/api/v1/auth/oauth-device/start", json={})
    body = r.json()
    dc, uc = body["device_code"], body["user_code"]

    r2 = await client.post("/api/v1/auth/oauth-device/poll",
                           json={"device_code": dc})
    assert r2.status_code == 400

    await asyncio.sleep(5.1)  # respect slow_down for next poll
    r3 = await client.post("/api/v1/auth/oauth-device/approve-dev",
                           json={"user_code": uc, "email": seeded_email})
    assert r3.status_code == 200, r3.text

    r4 = await client.post("/api/v1/auth/oauth-device/poll",
                           json={"device_code": dc})
    assert r4.status_code == 200, r4.text
    token = r4.json()
    assert token["token"]

    r5 = await client.get("/api/v1/auth/whoami",
                          headers={"Authorization": f"Bearer {token['token']}"})
    assert r5.status_code == 200
    assert r5.json()["email"].lower() == seeded_email.lower()


async def test_replay_device_code_after_consume_rejected(client, monkeypatch, seeded_email):
    monkeypatch.setenv("SEDIMENT_DEV_MODE", "1")
    r = await client.post("/api/v1/auth/oauth-device/start", json={})
    dc, uc = r.json()["device_code"], r.json()["user_code"]
    await client.post("/api/v1/auth/oauth-device/approve-dev",
                      json={"user_code": uc, "email": seeded_email})
    await asyncio.sleep(5.1)
    r2 = await client.post("/api/v1/auth/oauth-device/poll",
                           json={"device_code": dc})
    assert r2.status_code == 200, "first consume should mint"
    r3 = await client.post("/api/v1/auth/oauth-device/poll",
                           json={"device_code": dc})
    assert r3.status_code == 400, "second consume must reject"
    err = r3.json().get("detail", r3.json())
    assert err == {"error": "expired_token"}


async def test_user_code_uses_safe_alphabet(client):
    """No vowels / ambiguous chars in user codes (prevents OIO confusion)."""
    seen = set()
    for _ in range(20):
        r = await client.post("/api/v1/auth/oauth-device/start", json={})
        uc = r.json()["user_code"]
        seen.add(uc)
        for ch in uc.replace("-", ""):
            assert ch in "BCDFGHJKLMNPQRSTVWXZ", f"bad char {ch} in {uc}"
    assert len(seen) == 20


async def test_revoked_member_blocks_whoami(client, monkeypatch, seeded_email):
    from lab_lib.auth import _REVOKED_CACHE

    monkeypatch.setenv("SEDIMENT_DEV_MODE", "1")
    r = await client.post("/api/v1/auth/oauth-device/start", json={})
    dc, uc = r.json()["device_code"], r.json()["user_code"]
    await client.post("/api/v1/auth/oauth-device/approve-dev",
                      json={"user_code": uc, "email": seeded_email})
    await asyncio.sleep(5.1)
    r2 = await client.post("/api/v1/auth/oauth-device/poll",
                           json={"device_code": dc})
    token = r2.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    r3 = await client.get("/api/v1/auth/whoami", headers=headers)
    assert r3.status_code == 200

    try:
        async with service_session() as s:
            await s.execute(text(
                "UPDATE members SET revoked_at = now() WHERE lower(email) = lower(:e)"
            ), {"e": seeded_email})
            await s.commit()
        _REVOKED_CACHE.clear()
        r4 = await client.get("/api/v1/auth/whoami", headers=headers)
        assert r4.status_code == 401
    finally:
        async with service_session() as s:
            await s.execute(text(
                "UPDATE members SET revoked_at = NULL WHERE lower(email) = lower(:e)"
            ), {"e": seeded_email})
            await s.commit()
        _REVOKED_CACHE.clear()
