"""Edge cases for the MCP shim — companion to test_shim.py."""
from __future__ import annotations
import asyncio
import json
import os
import stat
import textwrap

import pytest

from sediment_mcp_shim import server


def _make_stub(tmp_path, script: str, name: str = "sediment"):
    p = tmp_path / name
    p.write_text(script)
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


@pytest.fixture
def use_stub(tmp_path, monkeypatch):
    def _setup(script: str):
        stub = _make_stub(tmp_path, script)
        monkeypatch.setenv("SEDIMENT_CLI", str(stub))
        monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + "/usr/bin:/bin")
        return stub
    return _setup


# ---------- timeout ----------

async def test_cli_hang_killed_after_timeout(use_stub, monkeypatch):
    """A CLI subprocess that sleeps forever must be killed."""
    use_stub(textwrap.dedent("""\
        #!/usr/bin/env bash
        sleep 200
    """))
    # Patch _call_cli to use a short timeout
    out = await server._call_cli(["whoami"], timeout=1.5)
    assert "error" in out
    assert out["error"]["reason"] == "timeout"


# ---------- huge payload ----------

async def test_cli_returns_huge_payload_handled(use_stub):
    """1MB JSON payload — must round-trip without OOM."""
    use_stub(textwrap.dedent("""\
        #!/usr/bin/env bash
        python3 -c 'import json; print(json.dumps({"items":[{"i":i,"data":"x"*100} for i in range(10000)]}))'
    """))
    out = await server._call_cli(["search", "x"])
    assert isinstance(out, dict)
    assert len(out["items"]) == 10000


# ---------- stderr-only output ----------

async def test_cli_stderr_only_surfaced(use_stub):
    use_stub(textwrap.dedent("""\
        #!/usr/bin/env bash
        echo 'config file not found' >&2
        exit 4
    """))
    out = await server._call_cli(["whoami"])
    assert "error" in out
    assert out["error"]["reason"] == "empty_response"
    assert "config file not found" in out["error"]["message"]


# ---------- BOM-prefixed JSON ----------

async def test_cli_bom_prefixed_json_handled(use_stub):
    """Some shells/tools prepend a UTF-8 BOM. The shim should still parse."""
    use_stub(textwrap.dedent("""\
        #!/usr/bin/env bash
        # \xef\xbb\xbf is UTF-8 BOM
        printf '\\xef\\xbb\\xbf{"ok":true}\\n'
    """))
    out = await server._call_cli(["whoami"])
    # json.loads doesn't strip BOM — the shim will surface a parse error.
    # This test documents the current behavior; if we add BOM-stripping
    # in v1.1, flip the assertion.
    assert "error" in out
    assert out["error"]["reason"] in ("non_json_response", "empty_response")


# ---------- concurrent calls ----------

async def test_concurrent_calls_independent_subprocesses(use_stub):
    """Three concurrent tool calls must not block each other."""
    use_stub(textwrap.dedent("""\
        #!/usr/bin/env bash
        sleep 0.5
        echo '{"ok":true}'
    """))
    import time
    start = time.monotonic()
    results = await asyncio.gather(
        server._call_cli(["whoami"]),
        server._call_cli(["whoami"]),
        server._call_cli(["whoami"]),
    )
    elapsed = time.monotonic() - start
    assert all(r == {"ok": True} for r in results)
    # If they were serial, elapsed ≥ 1.5s. Concurrent should be ~0.5s.
    assert elapsed < 1.2, f"calls did not parallelize: {elapsed:.2f}s"


# ---------- CLI path with spaces ----------

async def test_cli_path_with_spaces_in_directory(tmp_path, monkeypatch):
    """`SEDIMENT_CLI` pointing at a path with spaces must work."""
    spaced = tmp_path / "has space"
    spaced.mkdir()
    stub = spaced / "sediment"
    stub.write_text("#!/usr/bin/env bash\necho '{\"ok\":true}'\n")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("SEDIMENT_CLI", str(stub))
    monkeypatch.setenv("PATH", str(spaced) + os.pathsep + "/usr/bin:/bin")
    # Also patch module-level CLI_BIN, since the shim caches it at import.
    monkeypatch.setattr(server, "CLI_BIN", str(stub))
    out = await server._call_cli(["whoami"])
    assert out == {"ok": True}


# ---------- exit codes ----------

async def test_cli_exit_nonzero_with_valid_json_body_returns_body(use_stub):
    """When CLI exits non-zero but emits a JSON error envelope, the shim
    must surface that envelope (not wrap it)."""
    use_stub(textwrap.dedent("""\
        #!/usr/bin/env bash
        echo '{"error":{"code":403,"reason":"permission_denied","message":"nope"}}'
        exit 3
    """))
    out = await server._call_cli(["whoami"])
    assert out == {"error": {"code": 403, "reason": "permission_denied", "message": "nope"}}


async def test_cli_exit_127_command_not_found(tmp_path, monkeypatch):
    """SEDIMENT_CLI points to a non-existent file. The shim's check must
    happen before subprocess spawn."""
    monkeypatch.setenv("SEDIMENT_CLI", "/nonexistent/sediment-binary")
    monkeypatch.setenv("PATH", "/nowhere")
    monkeypatch.setattr(server, "CLI_BIN", "/nonexistent/sediment-binary")
    out = await server._call_cli(["whoami"])
    assert out["error"]["reason"] == "cli_not_installed"
