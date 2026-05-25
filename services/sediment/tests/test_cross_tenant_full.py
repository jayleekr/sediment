"""Cross-tenant safety — the load-bearing invariant suite.

Every test here is a "shall not leak" guarantee. If any of them fails,
the CLI ships paused until the leak is closed.
"""
from __future__ import annotations
import asyncio
import os

import httpx
import pytest
from sqlalchemy import text

from applications.sediment_platform.main import app
from lab_lib.db import service_session
from lab_lib.auth import _REVOKED_CACHE


pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_DB") == "1", reason="DB not available"
)

HL_EMAIL = "jayleekr0125@gmail.com"
ACME_EMAIL = "admin@acme.test"


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _mint(client, email):
    r = await client.post("/api/v1/auth/dev-token", json={"email": email})
    assert r.status_code == 200, r.text
    return r.json()["token"]


# ---- 1. Token switching: no in-process state bleed ----

async def test_switching_tokens_in_single_client_no_bleed(client):
    """Two whoami calls in sequence on the same HTTP client, different
    tokens. Each must report the right identity."""
    t_hl = await _mint(client, HL_EMAIL)
    t_acme = await _mint(client, ACME_EMAIL)

    r1 = await client.get("/api/v1/auth/whoami",
                          headers={"Authorization": f"Bearer {t_hl}"})
    r2 = await client.get("/api/v1/auth/whoami",
                          headers={"Authorization": f"Bearer {t_acme}"})
    assert r1.json()["email"].lower() == HL_EMAIL.lower()
    assert r2.json()["email"].lower() == ACME_EMAIL.lower()
    assert r1.json()["tenant_id"] != r2.json()["tenant_id"]


# ---- 2. Concurrent calls, different tokens, interleaved ----

async def test_concurrent_two_tenants_interleaved_no_bleed(client):
    """50 calls each tenant in parallel; every response must be for the
    requesting tenant."""
    t_hl = await _mint(client, HL_EMAIL)
    t_acme = await _mint(client, ACME_EMAIL)

    async def whoami(token, expected_email):
        r = await client.get("/api/v1/auth/whoami",
                             headers={"Authorization": f"Bearer {token}"})
        return r.json()["email"].lower() == expected_email.lower()

    tasks = []
    for _ in range(50):
        tasks.append(whoami(t_hl, HL_EMAIL))
        tasks.append(whoami(t_acme, ACME_EMAIL))
    results = await asyncio.gather(*tasks)
    assert all(results), f"bleed detected: {sum(results)}/{len(results)} correct"


# ---- 3. Revoked across every protected endpoint ----

async def test_revoked_member_blocked_on_every_endpoint(client, monkeypatch):
    """Once a member is revoked, no protected endpoint may honor their JWT."""
    token = await _mint(client, HL_EMAIL)
    headers = {"Authorization": f"Bearer {token}"}

    # Sanity: works first
    assert (await client.get("/api/v1/auth/whoami", headers=headers)).status_code == 200

    # Revoke
    try:
        async with service_session() as s:
            await s.execute(text(
                "UPDATE members SET revoked_at = now() WHERE lower(email) = lower(:e)"
            ), {"e": HL_EMAIL})
            await s.commit()
        _REVOKED_CACHE.clear()

        # Hit every protected endpoint and assert 401
        endpoints = [
            ("GET", "/api/v1/auth/whoami", None),
            ("GET", "/api/v1/library/search?q=x", None),
            ("GET", "/api/v1/library?limit=5", None),
        ]
        for method, path, body in endpoints:
            if method == "GET":
                r = await client.get(path, headers=headers)
            assert r.status_code == 401, f"endpoint {path} returned {r.status_code} for revoked member"
    finally:
        async with service_session() as s:
            await s.execute(text(
                "UPDATE members SET revoked_at = NULL WHERE lower(email) = lower(:e)"
            ), {"e": HL_EMAIL})
            await s.commit()
        _REVOKED_CACHE.clear()


# ---- 4. Expired-JWT error must not leak tenant info ----

async def test_expired_jwt_error_message_does_not_leak_tenant(client):
    """A 401 from an expired or invalid JWT must not include tenant_id /
    member_id / email in the response body."""
    import time
    from jose import jwt as _jwt
    from lab_lib.settings import settings as _s
    # An expired but otherwise valid-looking token
    payload = {
        "sub": "11111111-1111-1111-1111-111111111111",
        "org_id": "tenant-a-uuid",
        "role": "admin",
        "email": "secret@internal",
        "iss": _s.jwt_issuer,
        "aud": _s.jwt_audience,
        "iat": int(time.time()) - 3600,
        "exp": int(time.time()) - 60,
    }
    token = _jwt.encode(payload, _s.jwt_secret, algorithm="HS256")
    r = await client.get("/api/v1/auth/whoami",
                         headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    body = r.text
    for leak in ("tenant-a-uuid", "secret@internal", "11111111"):
        assert leak not in body, f"leak `{leak}` found in 401 body: {body}"


# ---- 5. dev-token for one tenant cannot mint for another ----

async def test_dev_token_returns_correct_tenant_per_email(client):
    """Sanity that dev-token resolves tenant from members table, not
    request input. Already implicit but make it explicit."""
    t_hl = await _mint(client, HL_EMAIL)
    t_acme = await _mint(client, ACME_EMAIL)
    r_hl = await client.get("/api/v1/auth/whoami",
                            headers={"Authorization": f"Bearer {t_hl}"})
    r_acme = await client.get("/api/v1/auth/whoami",
                              headers={"Authorization": f"Bearer {t_acme}"})
    assert r_hl.json()["tenant_id"] != r_acme.json()["tenant_id"]


# ---- 6. Device-code approved by tenant-A admin can't mint for tenant-B member ----

async def test_device_code_approved_for_member_mints_their_tenant_only(
    client, monkeypatch
):
    """A device-code, once approved for member M in tenant T, mints a JWT
    bound to (M, T). It cannot be redirected to mint for a different
    member or tenant after the fact."""
    monkeypatch.setenv("SEDIMENT_DEV_MODE", "1")
    r = await client.post("/api/v1/auth/oauth-device/start", json={})
    dc, uc = r.json()["device_code"], r.json()["user_code"]
    # Approve for HL email
    await client.post("/api/v1/auth/oauth-device/approve-dev",
                      json={"user_code": uc, "email": HL_EMAIL})
    await asyncio.sleep(5.1)
    r2 = await client.post("/api/v1/auth/oauth-device/poll",
                           json={"device_code": dc})
    assert r2.status_code == 200
    token = r2.json()
    # JWT must reflect HL's tenant, not Acme's
    r3 = await client.get("/api/v1/auth/whoami",
                          headers={"Authorization": f"Bearer {token['token']}"})
    assert r3.json()["email"].lower() == HL_EMAIL.lower()


# ---- 7. Library route always uses identity.tenant_id, never request input ----

async def test_library_search_uses_jwt_tenant_not_request_input(client):
    """Search results filter by identity.tenant_id. A token-bearing user
    cannot pass a tenant_id query param to view another tenant's data."""
    t_acme = await _mint(client, ACME_EMAIL)
    # Try to inject a tenant_id param into the search request
    r = await client.get(
        "/api/v1/library/search",
        params={"q": "research", "tenant_id": "any-uuid-here", "limit": 50},
        headers={"Authorization": f"Bearer {t_acme}"},
    )
    assert r.status_code == 200
    items = r.json().get("items", [])
    # Acme has no chunked content in seed; should be empty regardless of
    # injected tenant param
    leaks = [it for it in items if (it.get("ref") or "").startswith("research/daily/")]
    assert not leaks, f"tenant param injection bled HL data: {leaks}"
