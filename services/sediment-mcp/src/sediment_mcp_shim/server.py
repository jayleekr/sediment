"""sediment-mcp — Claude Code MCP server backed by the `sediment` CLI.

Design (per docs/design/cli-multi-user-access.md):
  - 5 tools mirror the CLI verbs: whoami, search, ask, read, recent.
  - Each tool shells out to the `sediment` binary with --format json.
  - The shim owns no state — auth/keychain belong to the CLI.
  - If the CLI is missing, the tools return a clear hint to install it.

Distribution: published as `sediment-mcp-shim` on PyPI. Users install via
`pipx install sediment-mcp-shim` and Claude Code registers it as:

  {
    "mcpServers": {
      "sediment": { "command": "sediment-mcp", "args": [] }
    }
  }
"""
from __future__ import annotations
import asyncio
import json
import os
import shutil
import sys
from typing import Any, Optional

from fastmcp import FastMCP


CLI_BIN = os.environ.get("SEDIMENT_CLI", "sediment")
mcp = FastMCP("sediment")


# ---------- CLI invocation helper ----------

async def _call_cli(args: list[str], *, timeout: float = 60.0) -> dict:
    """Run `sediment <args> --format json`, parse stdout, surface errors as
    MCP-friendly dicts. Never raises — always returns a dict (LLM-readable).
    """
    bin_path = shutil.which(CLI_BIN) or CLI_BIN
    if not shutil.which(CLI_BIN):
        return {
            "error": {
                "code": -2,
                "reason": "cli_not_installed",
                "message": f"sediment CLI not found on PATH (looked for `{CLI_BIN}`)",
                "hint": "brew install hypeprooflab/tap/sediment",
            }
        }

    proc = await asyncio.create_subprocess_exec(
        bin_path,
        "--format",
        "json",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return {
            "error": {
                "code": -3,
                "reason": "timeout",
                "message": f"sediment {' '.join(args)} timed out after {timeout}s",
            }
        }

    body = stdout.decode("utf-8", errors="replace").strip()
    if not body:
        # CLI emitted nothing on stdout. Either OK with no output, or it
        # printed an error to stderr only — surface stderr so the LLM can
        # see what's wrong.
        return {
            "error": {
                "code": proc.returncode or -1,
                "reason": "empty_response",
                "message": stderr.decode("utf-8", errors="replace").strip()[:400],
            }
        }
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return {
            "error": {
                "code": proc.returncode or -1,
                "reason": "non_json_response",
                "message": body[:400],
            }
        }
    return parsed


# ---------- Tools ----------

@mcp.tool()
async def sediment__whoami() -> dict:
    """Return the authenticated identity (member + tenant) for this CLI."""
    return await _call_cli(["whoami"])


@mcp.tool()
async def sediment__search(query: str, limit: int = 8,
                           type: Optional[str] = None) -> dict:
    """Hybrid search over the team's vault — ranked refs + excerpts.

    Args:
      query: free-text (Korean or English).
      limit: max results (1..50).
      type:  optional artifact-type filter.
    """
    args = ["search", query, "--limit", str(max(1, min(int(limit), 50)))]
    if type:
        args.extend(["--type", type])
    return await _call_cli(args)


@mcp.tool()
async def sediment__read(ref: str) -> dict:
    """Read one artifact's full body by ref path."""
    return await _call_cli(["read", ref])


@mcp.tool()
async def sediment__recent(days: int = 7, type: Optional[str] = None,
                           limit: int = 20) -> dict:
    """List artifacts added/updated in the last N days."""
    args = [
        "recent",
        "--days", str(max(1, min(int(days), 365))),
        "--limit", str(max(1, min(int(limit), 100))),
    ]
    if type:
        args.extend(["--type", type])
    return await _call_cli(args)


@mcp.tool()
async def sediment__ask(query: str) -> dict:
    """Ask the lab's memory a natural-language question.
    Returns the synthesized answer + citations."""
    # CLI without --stream emits the full result as JSON on stdout.
    return await _call_cli(["ask", query], timeout=120.0)


# ---------- Entry point ----------

def main() -> int:
    transport = os.environ.get("SEDIMENT_MCP_TRANSPORT", "stdio")
    if transport == "http":
        port = int(os.environ.get("SEDIMENT_MCP_PORT", "10030"))
        host = os.environ.get("SEDIMENT_MCP_HOST", "127.0.0.1")
        print(f"sediment-mcp-shim on http://{host}:{port}", file=sys.stderr)
        mcp.run(transport="http", host=host, port=port)
    else:
        mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
