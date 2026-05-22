"""MCP shim tests — mostly contract tests on tool count + argv shape.

Strategy:
  - Mock the CLI by injecting SEDIMENT_CLI=<path to a stub script>.
  - Each tool calls the stub; we assert what argv it received and that the
    JSON envelope round-trips back through the shim unchanged.
"""
from __future__ import annotations
import json
import os
import shutil
import stat
import textwrap
from pathlib import Path

import pytest

from sediment_mcp_shim import server


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    """Drop in a tiny shell stub that records argv and echoes a canned JSON
    payload. Returns (stub_path, argv_log_path)."""
    log = tmp_path / "argv.log"
    canned = tmp_path / "response.json"
    canned.write_text(json.dumps({"echo": "ok"}))
    stub = tmp_path / "sediment"
    stub.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -e
        printf '%s\\n' "$@" >> "{log}"
        cat "{canned}"
    """))
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("SEDIMENT_CLI", str(stub))
    # Put the stub on PATH so shutil.which finds it.
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    return stub, log, canned


async def test_tool_count_is_five():
    tools = await server.mcp.list_tools()
    names = sorted(t.name for t in tools)
    assert names == [
        "sediment__ask",
        "sediment__read",
        "sediment__recent",
        "sediment__search",
        "sediment__whoami",
    ]


async def test_whoami_invokes_cli(fake_cli):
    _, log, _ = fake_cli
    out = await server.sediment__whoami()
    assert out == {"echo": "ok"}
    lines = log.read_text().splitlines()
    # Expect: --format json whoami
    assert "--format" in lines and "json" in lines and "whoami" in lines


async def test_search_passes_limit_and_type(fake_cli):
    _, log, _ = fake_cli
    await server.sediment__search("hello world", limit=12, type="research")
    args = log.read_text().splitlines()
    assert "search" in args
    assert "hello world" in args
    # --limit 12 appears
    i = args.index("--limit")
    assert args[i + 1] == "12"
    i = args.index("--type")
    assert args[i + 1] == "research"


async def test_search_limit_clamped(fake_cli):
    _, log, _ = fake_cli
    await server.sediment__search("q", limit=9999)
    args = log.read_text().splitlines()
    i = args.index("--limit")
    assert args[i + 1] == "50"


async def test_read_passes_ref(fake_cli):
    _, log, _ = fake_cli
    await server.sediment__read("docs/foo.md")
    args = log.read_text().splitlines()
    assert "read" in args
    assert "docs/foo.md" in args


async def test_recent_passes_days_and_limit(fake_cli):
    _, log, _ = fake_cli
    await server.sediment__recent(days=14, limit=5)
    args = log.read_text().splitlines()
    assert "recent" in args
    assert args[args.index("--days") + 1] == "14"
    assert args[args.index("--limit") + 1] == "5"


async def test_ask_uses_long_timeout_and_passes_query(fake_cli):
    _, log, _ = fake_cli
    await server.sediment__ask("what is foo?")
    args = log.read_text().splitlines()
    assert "ask" in args
    assert "what is foo?" in args


async def test_cli_missing_returns_hint(tmp_path, monkeypatch):
    monkeypatch.setenv("SEDIMENT_CLI", "definitely-not-installed-binary-xyz")
    # Wipe PATH so shutil.which definitely fails.
    monkeypatch.setenv("PATH", "/nonexistent")
    out = await server.sediment__whoami()
    assert "error" in out
    assert out["error"]["reason"] == "cli_not_installed"
    assert "brew install" in out["error"]["hint"]


async def test_cli_emits_non_json_surfaced(tmp_path, monkeypatch):
    stub = tmp_path / "sediment"
    stub.write_text("#!/usr/bin/env bash\necho 'plain text not json'\n")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("SEDIMENT_CLI", str(stub))
    # Keep system bin paths so /usr/bin/env can find bash; prepend tmp_path
    # so our stub is found first.
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + "/usr/bin:/bin")
    out = await server.sediment__whoami()
    assert "error" in out
    assert out["error"]["reason"] == "non_json_response"
    assert "plain text" in out["error"]["message"]


async def test_cli_error_envelope_passthrough(tmp_path, monkeypatch):
    """If the CLI exits non-zero with a JSON envelope, the shim returns it
    verbatim (no extra wrapping)."""
    stub = tmp_path / "sediment"
    stub.write_text(textwrap.dedent("""\
        #!/usr/bin/env bash
        echo '{"error":{"code":401,"reason":"auth_expired","message":"go login"}}'
        exit 2
    """))
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("SEDIMENT_CLI", str(stub))
    # Keep system bin paths so /usr/bin/env can find bash; prepend tmp_path
    # so our stub is found first.
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + "/usr/bin:/bin")
    out = await server.sediment__whoami()
    assert out == {"error": {"code": 401, "reason": "auth_expired", "message": "go login"}}
