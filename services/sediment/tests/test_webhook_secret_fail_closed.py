"""sediment#115 — GitHub/Discord webhook signature must fail CLOSED in prod.

Before #115, `_verify_github_sig` returned True (skipped verification) whenever
GITHUB_WEBHOOK_SECRET was unset, in EVERY environment. Combined with #104's
tenant-by-repo resolution, an unset-secret prod deploy let any caller inject
vault data into any repo-mapped tenant (and forged Discord events into the
default tenant, since /webhook/discord-ingest shares the same verifier).

These tests are pure crypto/env logic and the auth-rejection path, so they need
no database and run under SKIP_DB=1.
"""
from __future__ import annotations

import hashlib
import hmac

import httpx
import pytest

from applications.vault_ingester.main import (
    _require_webhook_secret_in_prod,
    _verify_github_sig,
)

PROD_ENVS = ["SEDIMENT_ENV", "FLY_APP_NAME", "VERCEL"]


def _clear_prod_env(monkeypatch):
    for k in PROD_ENVS:
        monkeypatch.delenv(k, raising=False)


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Unit: _verify_github_sig
# ---------------------------------------------------------------------------

def test_unset_secret_rejects_in_prod(monkeypatch):
    """The core #115 fix: unset secret in prod => reject (fail-closed)."""
    _clear_prod_env(monkeypatch)
    monkeypatch.setenv("SEDIMENT_ENV", "prod")
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    assert _verify_github_sig(b'{"any": "body"}', None) is False


def test_unset_secret_rejects_in_prod_via_fly(monkeypatch):
    """FLY_APP_NAME is a prod signal too — same fail-closed outcome."""
    _clear_prod_env(monkeypatch)
    monkeypatch.setenv("FLY_APP_NAME", "sediment-prod")
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    assert _verify_github_sig(b"{}", None) is False


def test_unset_secret_skips_in_dev(monkeypatch):
    """Dev convenience preserved: unset secret + non-prod => accept."""
    _clear_prod_env(monkeypatch)
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    assert _verify_github_sig(b"{}", None) is True


def test_valid_signature_accepted(monkeypatch):
    """A correct HMAC is accepted regardless of environment."""
    _clear_prod_env(monkeypatch)
    monkeypatch.setenv("SEDIMENT_ENV", "prod")
    secret = "s3cret-webhook-value"
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", secret)
    body = b'{"ref": "abc", "files": []}'
    assert _verify_github_sig(body, _sign(secret, body)) is True


def test_invalid_signature_rejected(monkeypatch):
    """A wrong/forged HMAC is rejected even when the secret is configured."""
    _clear_prod_env(monkeypatch)
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cret-webhook-value")
    body = b'{"ref": "abc", "files": []}'
    assert _verify_github_sig(body, _sign("wrong-secret", body)) is False
    assert _verify_github_sig(body, None) is False
    assert _verify_github_sig(body, "not-sha256-prefixed") is False


# ---------------------------------------------------------------------------
# Boot guard: _require_webhook_secret_in_prod
# ---------------------------------------------------------------------------

def test_boot_guard_exits_in_prod_without_secret(monkeypatch):
    _clear_prod_env(monkeypatch)
    monkeypatch.setenv("SEDIMENT_ENV", "prod")
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    with pytest.raises(SystemExit):
        _require_webhook_secret_in_prod()


def test_boot_guard_noop_in_prod_with_secret(monkeypatch):
    _clear_prod_env(monkeypatch)
    monkeypatch.setenv("SEDIMENT_ENV", "prod")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cret-webhook-value")
    _require_webhook_secret_in_prod()  # must not raise


def test_boot_guard_noop_in_dev(monkeypatch):
    _clear_prod_env(monkeypatch)
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    _require_webhook_secret_in_prod()  # must not raise


# ---------------------------------------------------------------------------
# End-to-end: both webhook endpoints 401 in prod when the secret is unset.
# Auth rejection happens before any DB access, so no database is required.
# ---------------------------------------------------------------------------

@pytest.fixture
async def client():
    from applications.vault_ingester.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.parametrize("path", ["/webhook/ingest", "/webhook/discord-ingest"])
async def test_webhook_endpoint_401_in_prod_without_secret(client, monkeypatch, path):
    _clear_prod_env(monkeypatch)
    monkeypatch.setenv("SEDIMENT_ENV", "prod")
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    r = await client.post(path, content=b'{"messages": [], "files": []}')
    assert r.status_code == 401, f"{path} must fail-closed in prod, got {r.status_code}: {r.text}"
