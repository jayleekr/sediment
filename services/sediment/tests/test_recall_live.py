"""Regression tests for recall_live._mint_token token resolution (sediment T5).

Covers the auth path that was broken in prod: the nightly recall job minted a
JWT via /api/v1/auth/dev-token, which 403s under SEDIMENT_DEV_MODE. The fix is
to prefer an injected SEDIMENT_CI_TOKEN. These tests pin that behavior with a
mocked httpx client (no network, no DB) so they run anywhere.
"""
from __future__ import annotations

import pytest

from validator.scripts import recall_live


class _FakeResp:
    def __init__(self, status_code: int = 200, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Minimal stand-in for httpx.AsyncClient. Records POST calls."""

    def __init__(self, resp: _FakeResp | None = None, *, must_not_call: bool = False):
        self._resp = resp
        self._must_not_call = must_not_call
        self.calls = 0

    async def post(self, *args, **kwargs):
        self.calls += 1
        assert not self._must_not_call, "HTTP must not be called when a CI token is present"
        return self._resp


async def test_ci_token_used_directly_without_http(monkeypatch):
    monkeypatch.setenv("SEDIMENT_CI_TOKEN", "tok-123")
    client = _FakeClient(must_not_call=True)
    assert await recall_live._mint_token(client) == "tok-123"
    assert client.calls == 0


async def test_ci_token_is_stripped(monkeypatch):
    # A trailing newline (common from copy-paste / secret injection) must not
    # leak into the Authorization header.
    monkeypatch.setenv("SEDIMENT_CI_TOKEN", "  tok-123\n")
    client = _FakeClient(must_not_call=True)
    assert await recall_live._mint_token(client) == "tok-123"


async def test_ci_token_set_but_blank_raises(monkeypatch):
    monkeypatch.setenv("SEDIMENT_CI_TOKEN", "   ")
    client = _FakeClient(must_not_call=True)
    with pytest.raises(RuntimeError, match="empty"):
        await recall_live._mint_token(client)


async def test_falls_back_to_dev_token_when_unset(monkeypatch):
    monkeypatch.delenv("SEDIMENT_CI_TOKEN", raising=False)
    client = _FakeClient(resp=_FakeResp(200, {"token": "minted-local"}))
    assert await recall_live._mint_token(client) == "minted-local"
    assert client.calls == 1


async def test_dev_token_403_raises_actionable_error(monkeypatch):
    monkeypatch.delenv("SEDIMENT_CI_TOKEN", raising=False)
    client = _FakeClient(resp=_FakeResp(403))
    with pytest.raises(RuntimeError, match="SEDIMENT_CI_TOKEN"):
        await recall_live._mint_token(client)
