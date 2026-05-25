"""IT #4 — RLS cross-tenant isolation through the HTTP boundary.

This is the load-bearing safety test for multi-tenant CLI rollout.
Exercises the same shape the future Rust CLI will use:
   1. Mint JWT for Tenant A member  (via /dev-token)
   2. Mint JWT for Tenant B member
   3. GET /api/v1/library/search?q=<acme-only term>  with each token
   4. Assert: Tenant A token sees the marker artifact;
              Tenant B token sees ZERO matches.
   5. Symmetric: search for a hypeproof-lab-only term with Acme token →
              ZERO matches.

If this test ever passes for one tenant but fails for the other, RLS has
regressed and we MUST NOT ship the CLI.
"""
from __future__ import annotations
import os

import httpx
import pytest
from sqlalchemy import text

from applications.sediment_platform.main import app
from lab_lib.db import service_session


pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_DB") == "1", reason="DB not available"
)


HL_EMAIL = "jayleekr0125@gmail.com"        # hypeproof-lab tenant
ACME_EMAIL = "admin@acme.test"           # acme-test tenant


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                  base_url="http://test") as c:
        yield c


@pytest.fixture
async def both_tenants_seeded():
    """Skip if either tenant's marker artifact is missing."""
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT COUNT(*) FILTER (WHERE t.slug='acme-test')   AS acme,
                   COUNT(*) FILTER (WHERE t.slug='hypeproof-lab') AS hl
            FROM artifacts a JOIN tenants t ON a.tenant_id = t.id
            WHERE a.ref LIKE 'rls-test/%' OR a.ref LIKE 'validator/sample-%'
        """))
        row = r.first()
    if not row or row[0] == 0 or row[1] == 0:
        pytest.skip("RLS marker artifacts not seeded — run `make seed`")
    return True


async def _mint(client, email: str) -> str:
    r = await client.post("/api/v1/auth/dev-token", json={"email": email})
    assert r.status_code == 200, r.text
    return r.json()["token"]


async def test_acme_token_cannot_see_hypeproof_lab_artifacts(
    client, both_tenants_seeded
):
    """Tenant B (acme-test) token searching for a hypeproof-lab-only path
    must NOT see hypeproof-lab artifacts."""
    acme_token = await _mint(client, ACME_EMAIL)
    r = await client.get(
        "/api/v1/library/search",
        params={"q": "research/daily", "limit": 50},
        headers={"Authorization": f"Bearer {acme_token}"},
    )
    assert r.status_code == 200, r.text
    items = r.json().get("items", [])
    # Any item whose ref starts with research/daily/ would be a hypeproof-lab
    # artifact and a tenant bleed.
    leaks = [it for it in items if (it.get("ref") or "").startswith("research/daily/")]
    assert not leaks, f"RLS BLEED: acme token saw {len(leaks)} hypeproof-lab items"


async def test_hypeproof_lab_token_cannot_see_acme_artifacts(
    client, both_tenants_seeded
):
    """Tenant A (hypeproof-lab) token searching for an acme marker term
    must NOT see acme artifacts."""
    hl_token = await _mint(client, HL_EMAIL)
    r = await client.get(
        "/api/v1/library/search",
        params={"q": "acme-only-marker", "limit": 50},
        headers={"Authorization": f"Bearer {hl_token}"},
    )
    assert r.status_code == 200, r.text
    items = r.json().get("items", [])
    leaks = [it for it in items if "acme" in (it.get("ref") or "").lower()]
    assert not leaks, f"RLS BLEED: hl token saw {len(leaks)} acme items"


async def test_hypeproof_lab_token_sees_own_marker(client, both_tenants_seeded):
    """Sanity: search can see this tenant's own RLS marker chunk."""
    hl_token = await _mint(client, HL_EMAIL)
    r = await client.get(
        "/api/v1/library/search",
        params={"q": "lab-only-marker-RLS", "limit": 50},
        headers={"Authorization": f"Bearer {hl_token}"},
    )
    assert r.status_code == 200, r.text
    items = r.json().get("items", [])
    assert items, "expected hypeproof-lab token to see at least one item"


async def test_no_token_rejected(client):
    """An unauthenticated /search must 401, not silently return tenant data."""
    r = await client.get("/api/v1/library/search", params={"q": "research"})
    assert r.status_code == 401


async def test_invalid_token_rejected(client):
    """A garbage bearer must 401."""
    r = await client.get(
        "/api/v1/library/search",
        params={"q": "research"},
        headers={"Authorization": "Bearer not-a-real-jwt"},
    )
    assert r.status_code == 401
