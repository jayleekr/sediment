"""CORS policy tests (sediment#80).

Credentialed CORS must not trust the shared ``*.vercel.app`` apex. These tests
build a throwaway Starlette app from ``lab_lib.cors.build_cors_kwargs`` and
exercise real preflight requests, so they run WITHOUT a database (independent of
the SKIP_DB gate that guards test_security.py).
"""
from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from lab_lib.cors import _assert_safe, build_cors_kwargs

# The exact pre-fix (vulnerable) config from both main.py files.
_OLD_VULNERABLE_KWARGS = {
    "allow_origins": ["http://localhost:3000", "http://127.0.0.1:3000"],
    "allow_origin_regex": r"https://([a-z0-9-]+\.)?(vercel\.app|hypeproof-ai\.xyz|hypeproof\.studio)",
    "allow_credentials": True,
    "allow_methods": ["*"],
    "allow_headers": ["*"],
}


def _client(env: dict) -> TestClient:
    async def whoami(_request):
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/whoami", whoami, methods=["GET"])])
    app.add_middleware(CORSMiddleware, **build_cors_kwargs(env=env))
    return TestClient(app)


def _preflight_allow_origin(client: TestClient, origin: str) -> str:
    r = client.options(
        "/whoami",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )
    return r.headers.get("access-control-allow-origin", "")


def test_first_party_origins_allowed():
    client = _client({})
    for origin in [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://sediment.hypeproof-ai.xyz",
        "https://hypeproof-ai.xyz",
        "https://hypeproof.studio",
    ]:
        assert _preflight_allow_origin(client, origin) == origin


def test_arbitrary_vercel_preview_blocked_by_default():
    """The core sediment#80 fix: no team slug configured => the shared
    *.vercel.app apex is NOT trusted for credentialed requests."""
    client = _client({})
    for origin in [
        "https://evil.vercel.app",
        "https://attacker-app.vercel.app",
        "https://sediment-git-main-someotherteam.vercel.app",
        "https://vercel.app",
    ]:
        assert _preflight_allow_origin(client, origin) == "", f"{origin} must not be echoed"


def test_scoped_preview_allowed_only_for_configured_team_slug():
    client = _client({"SEDIMENT_VERCEL_TEAM_SLUG": "hypeprooflab"})
    good = "https://sediment-git-main-hypeprooflab.vercel.app"
    assert _preflight_allow_origin(client, good) == good
    # An off-team preview URL is still rejected even with a slug configured.
    for origin in [
        "https://evil.vercel.app",
        "https://sediment-git-main-otherteam.vercel.app",
    ]:
        assert _preflight_allow_origin(client, origin) == ""


def test_extra_origins_env_are_added():
    client = _client({"SEDIMENT_CORS_EXTRA_ORIGINS": "https://staging.hypeproof-ai.xyz"})
    origin = "https://staging.hypeproof-ai.xyz"
    assert _preflight_allow_origin(client, origin) == origin


def test_guard_rejects_old_broad_vercel_regex():
    """The exact pre-fix config must be refused by the safety guard, so a
    broad credentialed regex cannot silently regress back in."""
    with pytest.raises(ValueError):
        _assert_safe(_OLD_VULNERABLE_KWARGS)


def test_guard_rejects_wildcard_with_credentials():
    with pytest.raises(ValueError):
        _assert_safe({"allow_origins": ["*"], "allow_credentials": True})


def test_build_never_returns_broad_regex_even_with_slug():
    kwargs = build_cors_kwargs(env={"SEDIMENT_VERCEL_TEAM_SLUG": "hypeprooflab"})
    # build_cors_kwargs runs _assert_safe internally; assert the scoped regex
    # shape explicitly too.
    assert kwargs["allow_origin_regex"] == r"https://[a-z0-9-]+-hypeprooflab\.vercel\.app"
