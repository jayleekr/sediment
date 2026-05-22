"""End-to-end: shim's tool functions invoked against a real `sediment` CLI
binary, against a running Sediment platform.

We skip the JSON-RPC stdio layer (that's FastMCP's surface, already covered
by unit tests in test_shim.py) and directly call the @mcp.tool() functions
the way FastMCP would. What this validates:

  Python tool fn → asyncio subprocess → real `sediment` CLI binary →
                                       HTTPS → platform → DB → response

Gated by SEDIMENT_E2E_BASE_URL + SEDIMENT_E2E_TOKEN. When unset, skip.
"""
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
    """Point the shim at the worktree-built CLI binary + a working token."""
    # Prefer SEDIMENT_E2E_CLI explicit override; else find the worktree
    # debug binary; else fall back to PATH.
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
    # Reset module-level CLI_BIN since it's resolved at import time.
    from sediment_mcp_shim import server as srv
    monkeypatch.setattr(srv, "CLI_BIN", cli)
    return cli


async def test_e2e_whoami(real_cli_env):
    from sediment_mcp_shim import server as srv
    out = await srv.sediment__whoami()
    assert "email" in out and out["email"], out
    assert "member_id" in out
    assert "tenant_id" in out


async def test_e2e_search(real_cli_env):
    from sediment_mcp_shim import server as srv
    out = await srv.sediment__search("research", limit=3)
    assert "items" in out, out
    assert isinstance(out["items"], list)


async def test_e2e_read(real_cli_env):
    from sediment_mcp_shim import server as srv
    out = await srv.sediment__read("PHILOSOPHY.md")
    # Server returns the artifact body wrapped — the shim passes it through.
    assert out.get("ref") == "PHILOSOPHY.md", out


async def test_e2e_invalid_path_blocked_client_side(real_cli_env):
    """Path-traversal guard is in the CLI; shim surfaces the error envelope."""
    from sediment_mcp_shim import server as srv
    out = await srv.sediment__read("../../etc/passwd")
    assert "error" in out
    assert "path traversal" in out["error"]["message"].lower()
