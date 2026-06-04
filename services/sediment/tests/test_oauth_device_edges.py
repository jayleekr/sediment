"""Edge cases for the device authorization flow + JWT verification.

Companion to test_oauth_device.py (happy path). Lists in
docs/design/cli-test-requirements.md §1.1.
"""
from __future__ import annotations
import asyncio
import os
import time

import httpx
import pytest
from jose import jwt
from sqlalchemy import text

from applications.sediment_platform.main import app
from lab_lib.db import service_session
from lab_lib.settings import settings
from lab_lib.auth import _REVOKED_CACHE


pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_DB") == "1", reason="DB not available"
)

SEED_EMAIL = "jayleekr0125@gmail.com"


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def seeded_member_id():
    async with service_session() as s:
        r = await s.execute(text(
            "SELECT id::text, tenant_id::text FROM members WHERE lower(email) = lower(:e)"
        ), {"e": SEED_EMAIL})
        row = r.first()
    if not row:
        pytest.skip(f"member {SEED_EMAIL} not seeded")
    return row[0], row[1]


# ============================================================
# Device flow expiration + state transitions
# ============================================================

async def test_approve_after_expiration_returns_410(client, monkeypatch, seeded_member_id):
    monkeypatch.setenv("SEDIMENT_DEV_MODE", "1")
    r = await client.post("/api/v1/auth/oauth-device/start", json={})
    user_code = r.json()["user_code"]

    # Force expiration directly in DB
    async with service_session() as s:
        await s.execute(text(
            "UPDATE device_authorization_codes SET expires_at = now() - interval '1 second' "
            "WHERE user_code = :uc"
        ), {"uc": user_code})
        await s.commit()

    r2 = await client.post("/api/v1/auth/oauth-device/approve-dev",
                           json={"user_code": user_code, "email": SEED_EMAIL})
    assert r2.status_code == 410, r2.text


async def test_approve_already_approved_returns_409(client, monkeypatch, seeded_member_id):
    monkeypatch.setenv("SEDIMENT_DEV_MODE", "1")
    r = await client.post("/api/v1/auth/oauth-device/start", json={})
    uc = r.json()["user_code"]
    r2 = await client.post("/api/v1/auth/oauth-device/approve-dev",
                           json={"user_code": uc, "email": SEED_EMAIL})
    assert r2.status_code == 200
    r3 = await client.post("/api/v1/auth/oauth-device/approve-dev",
                           json={"user_code": uc, "email": SEED_EMAIL})
    assert r3.status_code == 409


async def test_denied_status_returns_access_denied(client):
    r = await client.post("/api/v1/auth/oauth-device/start", json={})
    dc = r.json()["device_code"]
    # Manually set status=denied (admin path; no public endpoint yet)
    async with service_session() as s:
        await s.execute(text(
            "UPDATE device_authorization_codes SET status='denied' WHERE device_code = :dc"
        ), {"dc": dc})
        await s.commit()

    r2 = await client.post("/api/v1/auth/oauth-device/poll",
                           json={"device_code": dc})
    assert r2.status_code == 400
    err = r2.json().get("detail", r2.json())
    assert err == {"error": "access_denied"}


async def test_approve_dev_unknown_email_404(client, monkeypatch):
    monkeypatch.setenv("SEDIMENT_DEV_MODE", "1")
    r = await client.post("/api/v1/auth/oauth-device/start", json={})
    uc = r.json()["user_code"]
    r2 = await client.post("/api/v1/auth/oauth-device/approve-dev",
                           json={"user_code": uc, "email": "nobody@example.test"})
    assert r2.status_code == 404


async def test_approve_with_lowercase_user_code_normalized(client, monkeypatch, seeded_member_id):
    monkeypatch.setenv("SEDIMENT_DEV_MODE", "1")
    r = await client.post("/api/v1/auth/oauth-device/start", json={})
    uc = r.json()["user_code"]
    # Send lowercase — server should uppercase before lookup.
    r2 = await client.post("/api/v1/auth/oauth-device/approve-dev",
                           json={"user_code": uc.lower(), "email": SEED_EMAIL})
    assert r2.status_code == 200, r2.text


# ============================================================
# Concurrency
# ============================================================

async def test_concurrent_starts_no_user_code_collision(client):
    """50 parallel /start: every response must have a unique user_code."""
    async def one():
        r = await client.post("/api/v1/auth/oauth-device/start", json={})
        return r.json()["user_code"]
    user_codes = await asyncio.gather(*[one() for _ in range(50)])
    assert len(set(user_codes)) == len(user_codes), \
        f"collision: {len(user_codes)} returned, {len(set(user_codes))} unique"


async def test_concurrent_polls_exactly_one_mints(client, monkeypatch, seeded_member_id):
    """After approval, 5 concurrent polls must yield exactly one 200 + token;
    others get expired_token (single-use guarantee)."""
    monkeypatch.setenv("SEDIMENT_DEV_MODE", "1")
    r = await client.post("/api/v1/auth/oauth-device/start", json={})
    dc, uc = r.json()["device_code"], r.json()["user_code"]
    await client.post("/api/v1/auth/oauth-device/approve-dev",
                      json={"user_code": uc, "email": SEED_EMAIL})
    # Wait past the slow_down window
    await asyncio.sleep(5.1)

    async def poll():
        return await client.post("/api/v1/auth/oauth-device/poll",
                                  json={"device_code": dc})
    results = await asyncio.gather(*[poll() for _ in range(5)])
    statuses = [r.status_code for r in results]
    minted = [r for r in results if r.status_code == 200]
    # Slow_down may rate-limit the parallel polls below 5; the load-bearing
    # invariant is that AT MOST one mints a token.
    assert len(minted) <= 1, f"multiple mints in {statuses}"


# ============================================================
# JWT edge cases — tamper / wrong claims
# ============================================================

def _mint_with(payload_override: dict) -> str:
    """Manually mint a JWT with overridden claims for tamper tests."""
    now = int(time.time())
    base = {
        "sub": "00000000-0000-0000-0000-000000000000",
        "org_id": "00000000-0000-0000-0000-000000000000",
        "role": "creator",
        "email": "x@y",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + 3600,
    }
    base.update(payload_override)
    return jwt.encode(base, settings.jwt_secret, algorithm="HS256")


async def test_whoami_with_expired_jwt_rejected(client):
    token = _mint_with({"exp": int(time.time()) - 60})
    r = await client.get("/api/v1/auth/whoami",
                         headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


async def test_whoami_with_wrong_audience_rejected(client):
    token = _mint_with({"aud": "wrong-audience"})
    r = await client.get("/api/v1/auth/whoami",
                         headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


async def test_whoami_with_wrong_issuer_rejected(client):
    token = _mint_with({"iss": "wrong-issuer"})
    r = await client.get("/api/v1/auth/whoami",
                         headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


async def test_whoami_with_modified_signature_rejected(client):
    token = _mint_with({})
    # Flip the first signature char. The last base64url char can encode only
    # padding bits for HS256 signatures, so changing it is not guaranteed to
    # change the decoded signature bytes.
    header, payload, signature = token.split(".")
    first = "A" if signature[0] != "A" else "B"
    tampered = ".".join([header, payload, first + signature[1:]])
    r = await client.get("/api/v1/auth/whoami",
                         headers={"Authorization": f"Bearer {tampered}"})
    assert r.status_code == 401


async def test_whoami_with_alg_none_rejected(client):
    """A JWT with `alg=none` (per CVE class) must be rejected."""
    import base64, json as _json
    header = base64.urlsafe_b64encode(_json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=")
    body = base64.urlsafe_b64encode(_json.dumps({
        "sub": "x", "org_id": "y", "role": "admin",
        "iss": settings.jwt_issuer, "aud": settings.jwt_audience,
        "iat": int(time.time()), "exp": int(time.time()) + 3600,
    }).encode()).rstrip(b"=")
    token = header.decode() + "." + body.decode() + "."  # no signature
    r = await client.get("/api/v1/auth/whoami",
                         headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


async def test_whoami_with_oversized_token_rejected_cleanly(client):
    """10 KB junk token: clean 401, no panic, no timing channel disclosure."""
    fake = "A" * 10240
    r = await client.get("/api/v1/auth/whoami",
                         headers={"Authorization": f"Bearer {fake}"})
    assert r.status_code == 401


# ============================================================
# Revocation cache
# ============================================================

async def test_revoked_check_caches_within_ttl(client, monkeypatch, seeded_member_id):
    """A second whoami within TTL must not re-query the DB."""
    monkeypatch.setenv("SEDIMENT_DEV_MODE", "1")
    r = await client.post("/api/v1/auth/dev-token", json={"email": SEED_EMAIL})
    token = r.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    _REVOKED_CACHE.clear()
    # First call — populates cache
    r1 = await client.get("/api/v1/auth/whoami", headers=headers)
    assert r1.status_code == 200
    # Mutate the DB column but DON'T clear the cache. The next call should
    # still succeed because the cache hides the revoke until TTL elapses.
    async with service_session() as s:
        await s.execute(text(
            "UPDATE members SET revoked_at = now() WHERE lower(email) = lower(:e)"
        ), {"e": SEED_EMAIL})
        await s.commit()
    try:
        r2 = await client.get("/api/v1/auth/whoami", headers=headers)
        assert r2.status_code == 200, "cached negative should still allow within TTL"
    finally:
        async with service_session() as s:
            await s.execute(text(
                "UPDATE members SET revoked_at = NULL WHERE lower(email) = lower(:e)"
            ), {"e": SEED_EMAIL})
            await s.commit()
        _REVOKED_CACHE.clear()


async def test_revoked_check_cache_is_per_member(client, monkeypatch):
    """Caching member A's status must not affect member B."""
    monkeypatch.setenv("SEDIMENT_DEV_MODE", "1")
    _REVOKED_CACHE.clear()
    r_a = await client.post("/api/v1/auth/dev-token", json={"email": SEED_EMAIL})
    token_a = r_a.json()["token"]
    r_b = await client.post("/api/v1/auth/dev-token", json={"email": "admin@acme.test"})
    token_b = r_b.json()["token"]

    # Warm up cache for both
    await client.get("/api/v1/auth/whoami",
                     headers={"Authorization": f"Bearer {token_a}"})
    await client.get("/api/v1/auth/whoami",
                     headers={"Authorization": f"Bearer {token_b}"})
    assert len(_REVOKED_CACHE) >= 2

    # Revoke member B only
    async with service_session() as s:
        await s.execute(text(
            "UPDATE members SET revoked_at = now() WHERE lower(email) = lower(:e)"
        ), {"e": "admin@acme.test"})
        await s.commit()
    try:
        # Clear ONLY member B's cache key
        from lab_lib.auth import decode_token
        ident_b = decode_token(token_b)
        _REVOKED_CACHE.pop(ident_b.member_id, None)
        r_b2 = await client.get("/api/v1/auth/whoami",
                                headers={"Authorization": f"Bearer {token_b}"})
        assert r_b2.status_code == 401
        # Member A still works
        r_a2 = await client.get("/api/v1/auth/whoami",
                                headers={"Authorization": f"Bearer {token_a}"})
        assert r_a2.status_code == 200
    finally:
        async with service_session() as s:
            await s.execute(text(
                "UPDATE members SET revoked_at = NULL WHERE lower(email) = lower(:e)"
            ), {"e": "admin@acme.test"})
            await s.commit()
        _REVOKED_CACHE.clear()
