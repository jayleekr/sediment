"""sediment_mcp — Claude Code MCP server for Sediment.

Exposes Sediment's team-facing surface as MCP tools so any Claude Code
session can query the shared knowledge vault, file decisions, and read
recent activity — without leaving the terminal.

Transport: stdio (default) so Claude Code spawns one process per session.
HTTP transport also supported via `transport=http` env for shared/remote.

Auth model:
  - SEDIMENT_BASE_URL   (default http://localhost:10100)
  - SEDIMENT_LG_BASE    (default http://localhost:10020)
  - SEDIMENT_TOKEN      (JWT — minted via /api/v1/auth/dev-token in local dev,
                       Discord OAuth in production)

Tools (intentionally small, high-leverage set — not the 12-tool firehose
of workspace_mcp.py which is for the internal langgraph agent):

  sediment__ask          natural-language Q&A with citations (calls langgraph
                         stream, accumulates full answer)
  sediment__search       hybrid search returning ranked refs+excerpts
  sediment__read         fetch one artifact body by ref
  sediment__recent       what's new in the vault (last N days)
  sediment__whoami       echoes the auth context — verify the JWT
"""
from __future__ import annotations
import json
import os
import re
import sys
from typing import Optional

import httpx
from fastmcp import FastMCP

SEDIMENT_BASE_URL = os.environ.get("SEDIMENT_BASE_URL", "http://localhost:10100")
SEDIMENT_LG_BASE = os.environ.get("SEDIMENT_LG_BASE", "http://localhost:10020")
SEDIMENT_TOKEN = os.environ.get("SEDIMENT_TOKEN", "")

mcp = FastMCP("sediment")


def _auth_headers() -> dict[str, str]:
    if not SEDIMENT_TOKEN:
        return {}
    return {"Authorization": f"Bearer {SEDIMENT_TOKEN}"}


@mcp.tool()
async def sediment__whoami() -> dict:
    """Return the authenticated identity (member + tenant) for this session.

    Useful first call to verify SEDIMENT_TOKEN works. If this fails the rest
    will fail too — fix the token before debugging anything else.
    """
    if not SEDIMENT_TOKEN:
        return {"error": "SEDIMENT_TOKEN not set. Run /sediment:setup or set env var."}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{SEDIMENT_BASE_URL}/api/v1/auth/whoami",
                              headers=_auth_headers())
        if r.status_code != 200:
            return {"error": f"{r.status_code}: {r.text[:200]}"}
        return r.json()


@mcp.tool()
async def sediment__search(query: str, limit: int = 8,
                           type: Optional[str] = None) -> list[dict]:
    """Hybrid search over the team's vault. Returns ranked chunks with refs.

    Use when you need the raw retrieval — citations, not a synthesized answer.
    For Q&A use `sediment__ask` instead.

    Args:
      query: free-text query (Korean or English).
      limit: max results (default 8, max 50).
      type:  optional filter — 'column' | 'research' | 'novel' | 'note' |
             'decision' | 'meeting'.
    """
    params: dict = {"q": query, "limit": min(limit, 50)}
    if type:
        params["type"] = type
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{SEDIMENT_BASE_URL}/api/v1/library/search",
                              params=params, headers=_auth_headers())
        r.raise_for_status()
        items = r.json().get("items", [])
    # Trim chunk content to keep token footprint reasonable.
    return [
        {"ref": it.get("ref"), "type": it.get("type"), "score": it.get("score"),
         "excerpt": (it.get("content") or "")[:400]}
        for it in items
    ]


@mcp.tool()
async def sediment__read(ref: str) -> dict:
    """Read one artifact's full body by ref path (e.g. 'PHILOSOPHY.md').

    Refs come from `sediment__search` results or are repo-relative paths the
    user already knows.
    """
    async with httpx.AsyncClient(timeout=15) as client:
        # /api/v1/library/{ref:path} — pass ref unencoded as path component
        r = await client.get(f"{SEDIMENT_BASE_URL}/api/v1/library/{ref}",
                              headers=_auth_headers())
        if r.status_code == 404:
            return {"error": f"not found: {ref}"}
        r.raise_for_status()
        d = r.json()
    return {
        "ref": d.get("ref"),
        "type": d.get("type"),
        "author": d.get("author_name"),
        "date": d.get("date"),
        "body": d.get("body") or "",
    }


@mcp.tool()
async def sediment__ask(query: str) -> dict:
    """Ask the lab's memory a natural-language question. Returns the LLM's
    synthesized answer plus the citations it used.

    Use this when you want a synthesized answer — not raw retrieval.
    The answer comes from the same LangGraph pipeline that powers the web
    UI, so quality (and any limitations like offline LLM mock) matches.

    Args:
      query: the question, in Korean or English.
    """
    # First create a transient conversation, then stream the answer.
    async with httpx.AsyncClient(timeout=60) as client:
        cr = await client.post(
            f"{SEDIMENT_BASE_URL}/api/v1/conversations",
            json={"title": query[:60]},
            headers=_auth_headers(),
        )
        cr.raise_for_status()
        cid = cr.json()["id"]

        # Consume SSE stream and collect deltas + citations.
        answer_chunks: list[str] = []
        citations: list[dict] = []
        async with client.stream(
            "POST", f"{SEDIMENT_LG_BASE}/v1/sediment/stream",
            json={"conv_id": cid, "query": query},
            headers=_auth_headers(), timeout=60,
        ) as resp:
            resp.raise_for_status()
            buf = ""
            cur_event = "message"
            async for chunk in resp.aiter_bytes():
                buf += chunk.decode("utf-8", errors="ignore")
                while "\n\n" in buf:
                    frame, buf = buf.split("\n\n", 1)
                    cur_event = "message"
                    data_lines = []
                    for line in frame.split("\n"):
                        if line.startswith("event: "):
                            cur_event = line[7:].strip()
                        elif line.startswith("data: "):
                            data_lines.append(line[6:])
                    data = "\n".join(data_lines)
                    if data == "[DONE]":
                        break
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if cur_event == "delta":
                        answer_chunks.append(payload.get("v", ""))
                    elif cur_event == "citation":
                        c = payload.get("v", {})
                        citations.append({
                            "type": c.get("type"),
                            "ref": c.get("ref"),
                            "display_name": c.get("display_name"),
                        })

    answer = "".join(answer_chunks).strip()
    # Strip the offline-mock sentinel so callers see a clear signal.
    if answer.startswith("[offline LLM mock]"):
        return {"answer": "", "citations": citations, "conv_id": cid,
                "warning": "service is running with offline LLM mock — "
                           "citations are real, but answer text is stubbed. "
                           "Set LLM_PROVIDER=anthropic on the langgraph service."}
    return {"answer": answer, "citations": citations, "conv_id": cid}


@mcp.tool()
async def sediment__recent(days: int = 7, type: Optional[str] = None,
                           limit: int = 20) -> list[dict]:
    """List artifacts added or updated in the last N days.

    Args:
      days:  lookback window (default 7).
      type:  optional artifact-type filter.
      limit: max results.
    """
    from datetime import date, timedelta
    df = (date.today() - timedelta(days=days)).isoformat()
    params: dict = {"date_from": df, "limit": min(limit, 100)}
    if type:
        params["type"] = type
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{SEDIMENT_BASE_URL}/api/v1/library",
                              params=params, headers=_auth_headers())
        r.raise_for_status()
        items = r.json().get("items", [])
    return [
        {"ref": it.get("ref"), "type": it.get("type"),
         "date": it.get("date"), "author": it.get("author_name")}
        for it in items
    ]


def main():
    transport = os.environ.get("SEDIMENT_MCP_TRANSPORT", "stdio")
    if transport == "http":
        port = int(os.environ.get("SEDIMENT_MCP_PORT", "10030"))
        host = os.environ.get("SEDIMENT_MCP_HOST", "127.0.0.1")
        print(f"sediment_mcp on http://{host}:{port}", file=sys.stderr)
        mcp.run(transport="http", host=host, port=port)
    else:
        # stdio is the default for Claude Code MCP — CC spawns the process
        # per-session and pipes stdin/stdout.
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
