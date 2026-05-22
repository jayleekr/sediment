"""Expanded MCP shim E2E — real CLI + live platform."""
from __future__ import annotations
import os
import shutil

import pytest


SEDIMENT_E2E_BASE_URL = os.environ.get("SEDIMENT_E2E_BASE_URL")
SEDIMENT_E2E_TOKEN = os.environ.get("SEDIMENT_E2E_TOKEN")

pytestmark = pytest.mark.skipif(
    not (SEDIMENT_E2E_BASE_URL and SEDIMENT_E2E_TOKEN),
    reason="set SEDIMENT_E2E_BASE_URL and SEDIMENT_E2E_TOKEN to run",
)


@pytest.fixture(autouse=True)
def real_cli_env(monkeypatch):
    cli = os.environ.get("SEDIMENT_E2E_CLI")
    if not cli:
        cli = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..",
                         "sediment-cli", "target", "debug", "sediment")
        )
        if not os.path.exists(cli):
            cli = shutil.which("sediment") or "sediment"
    monkeypatch.setenv("SEDIMENT_CLI", cli)
    monkeypatch.setenv("SEDIMENT_BASE_URL", SEDIMENT_E2E_BASE_URL)
    monkeypatch.setenv("SEDIMENT_TOKEN", SEDIMENT_E2E_TOKEN)
    from sediment_mcp_shim import server as srv
    monkeypatch.setattr(srv, "CLI_BIN", cli)
    return cli


async def test_e2e_recent_via_shim(real_cli_env):
    from sediment_mcp_shim import server as srv
    out = await srv.sediment__recent(days=365, limit=5)
    assert "items" in out
    assert isinstance(out["items"], list)


async def test_e2e_search_type_filter_via_shim(real_cli_env):
    from sediment_mcp_shim import server as srv
    out = await srv.sediment__search("research", limit=3, type="research")
    assert "items" in out
    for it in out["items"]:
        assert it.get("type") == "research", f"type filter leaked: {it}"


async def test_e2e_concurrent_calls_independent(real_cli_env):
    """Five concurrent shim tool calls — all should succeed independently."""
    import asyncio
    from sediment_mcp_shim import server as srv
    results = await asyncio.gather(
        srv.sediment__whoami(),
        srv.sediment__search("research", limit=2),
        srv.sediment__whoami(),
        srv.sediment__recent(days=30, limit=2),
        srv.sediment__whoami(),
    )
    for r in results:
        assert "error" not in r, r


async def test_e2e_shim_with_invalid_token_returns_auth_envelope(monkeypatch, real_cli_env):
    """Set a bogus token — shim should surface auth_expired envelope."""
    monkeypatch.setenv("SEDIMENT_TOKEN",
                       "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ4In0.bogus")
    from sediment_mcp_shim import server as srv
    out = await srv.sediment__whoami()
    assert "error" in out
    assert out["error"]["code"] == 401


async def test_e2e_shim_read_path_traversal_blocked(real_cli_env):
    """CLI's client-side path-traversal guard must surface a clear envelope."""
    from sediment_mcp_shim import server as srv
    out = await srv.sediment__read("../../etc/passwd")
    assert "error" in out
    assert "path traversal" in out["error"]["message"].lower()
