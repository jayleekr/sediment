"""Regression tests for the GitHub OAuth exchange path."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from applications.sediment_platform.routers import auth


@pytest.mark.asyncio
async def test_oauth_exchange_requires_access_token():
    req = auth.OAuthExchangeReq(provider="github", github_login="jayleekr")

    with pytest.raises(HTTPException) as exc:
        await auth.oauth_exchange(req)

    assert exc.value.status_code == 400
    assert exc.value.detail == "oauth-exchange requires access_token"


@pytest.mark.asyncio
async def test_oauth_exchange_uses_verified_github_login_not_client_spoof(monkeypatch):
    async def fake_fetch_github_identity(access_token: str) -> str:
        assert access_token == "real-token"
        return "jayleekr"

    class FakeResult:
        def first(self):
            return (
                "member-id",
                "tenant-id",
                "admin",
                "Jay Lee",
                "jay@example.test",
            )

    class FakeSession:
        async def execute(self, _query, params):
            assert params == {"gh": "jayleekr"}
            return FakeResult()

    class FakeServiceSession:
        async def __aenter__(self):
            return FakeSession()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(auth, "_fetch_github_identity", fake_fetch_github_identity)
    monkeypatch.setattr(auth, "service_session", lambda: FakeServiceSession())

    req = auth.OAuthExchangeReq(
        provider="github",
        github_login="someone-else",
        verified_emails=["spoof@example.test"],
        access_token="real-token",
    )

    resp = await auth.oauth_exchange(req)

    assert resp.member_id == "member-id"
    assert resp.tenant_id == "tenant-id"
    assert resp.role == "admin"
    assert resp.display_name == "Jay Lee"


@pytest.mark.asyncio
async def test_oauth_exchange_can_target_kids_edu_tenant(monkeypatch):
    async def fake_fetch_github_identity(_access_token: str) -> str:
        return "jayleekr"

    class FakeResult:
        def first(self):
            return (
                "kids-member-id",
                "kids-tenant-id",
                "admin",
                "Jay Lee",
                "jayleekr0125@gmail.com",
            )

    class FakeSession:
        async def execute(self, query, params):
            assert params == {"gh": "jayleekr", "tenant_slug": "kids-edu"}
            assert "t.slug = :tenant_slug" in str(query)
            return FakeResult()

    class FakeServiceSession:
        async def __aenter__(self):
            return FakeSession()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(auth, "_fetch_github_identity", fake_fetch_github_identity)
    monkeypatch.setattr(auth, "service_session", lambda: FakeServiceSession())

    resp = await auth.oauth_exchange(auth.OAuthExchangeReq(
        provider="github",
        access_token="real-token",
        tenant_slug="kids-edu",
    ))

    assert resp.member_id == "kids-member-id"
    assert resp.tenant_id == "kids-tenant-id"


@pytest.mark.asyncio
async def test_dev_token_can_target_kids_edu_tenant(monkeypatch):
    class FakeResult:
        def first(self):
            return (
                "kids-member-id",
                "kids-tenant-id",
                "admin",
                "Jay Lee",
            )

    class FakeSession:
        async def execute(self, query, params):
            assert params == {
                "email": "jayleekr0125@gmail.com",
                "tenant_slug": "kids-edu",
            }
            assert "t.slug = :tenant_slug" in str(query)
            return FakeResult()

    class FakeServiceSession:
        async def __aenter__(self):
            return FakeSession()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setenv("SEDIMENT_DEV_MODE", "1")
    monkeypatch.setattr(auth, "service_session", lambda: FakeServiceSession())

    resp = await auth.dev_token(auth.DevTokenReq(
        email="jayleekr0125@gmail.com",
        tenant_slug="kids-edu",
    ))

    assert resp.member_id == "kids-member-id"
    assert resp.tenant_id == "kids-tenant-id"
