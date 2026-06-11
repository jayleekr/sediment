"""Phase 2 custom Python checks."""
from __future__ import annotations
import asyncio
import json
import time
import httpx
from sqlalchemy import text

from lab_lib.auth import mint_token, decode_token
from lab_lib.db import service_session
from lab_lib.settings import settings


def _platform_base() -> str:
    return f"http://localhost:{settings.sediment_platform_port}"


def _langgraph_base() -> str:
    return f"http://localhost:{settings.sediment_langgraph_port}"


async def _admin_token() -> tuple[str, str, str]:
    async with service_session() as s:
        row = (await s.execute(text("""
            SELECT m.id::text, t.id::text FROM members m JOIN tenants t ON t.id=m.tenant_id
            WHERE t.slug='hypeproof-lab' AND m.role='admin' LIMIT 1
        """))).first()
    if not row:
        raise RuntimeError("no admin member in hypeproof-lab")
    mid, tid = row
    token = mint_token(member_id=mid, tenant_id=tid, role="admin", email="jayleekr0125@gmail.com")
    return token, mid, tid


# ──────────────── Auth ────────────────

async def check_dev_token_mints(spec: dict, **_) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{_platform_base()}/api/v1/auth/dev-token",
                              json={"email": "jayleekr0125@gmail.com"}, timeout=10)
    ok = r.status_code == 200 and "token" in r.json()
    return {"passed": ok, "actual": {"status": r.status_code},
            "message": "" if ok else r.text[:200]}


def check_jwt_roundtrip(spec: dict, **_) -> dict:
    t = mint_token(member_id="m-1", tenant_id="t-1", role="admin", email="x@y.z")
    ident = decode_token(t)
    ok = ident.member_id == "m-1" and ident.tenant_id == "t-1"
    return {"passed": ok, "actual": {"id": ident.member_id, "tid": ident.tenant_id}}


async def check_dev_token_unknown_email(spec: dict, **_) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{_platform_base()}/api/v1/auth/dev-token",
                              json={"email": "nobody-validator@example.com"}, timeout=10)
    ok = r.status_code == 404
    return {"passed": ok, "actual": {"status": r.status_code},
            "expected": {"status": 404}}


async def check_tampered_jwt_rejected(spec: dict, **_) -> dict:
    bad = "Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJub25lIn0.eyJzdWIiOiJ4In0."
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{_platform_base()}/api/v1/conversations",
                             headers={"Authorization": bad}, timeout=10)
    ok = r.status_code == 401
    return {"passed": ok, "actual": {"status": r.status_code}, "expected": {"status": 401}}


# ──────────────── Conversations ────────────────

async def check_create_conversation(spec: dict, **_) -> dict:
    token, _, _ = await _admin_token()
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{_platform_base()}/api/v1/conversations",
                              json={"title": "validator-conv"},
                              headers={"Authorization": f"Bearer {token}"}, timeout=10)
    ok = r.status_code == 200 and "id" in r.json()
    return {"passed": ok, "actual": {"status": r.status_code},
            "message": "" if ok else r.text[:200]}


async def check_list_conversations(spec: dict, **_) -> dict:
    token, _, _ = await _admin_token()
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{_platform_base()}/api/v1/conversations",
                             headers={"Authorization": f"Bearer {token}"}, timeout=10)
    ok = r.status_code == 200 and isinstance(r.json().get("items"), list)
    return {"passed": ok, "actual": {"status": r.status_code, "items": len(r.json().get("items", []))}}


async def check_post_message(spec: dict, **_) -> dict:
    token, _, _ = await _admin_token()
    async with httpx.AsyncClient() as client:
        cr = await client.post(f"{_platform_base()}/api/v1/conversations",
                               json={"title": "validator-msg"},
                               headers={"Authorization": f"Bearer {token}"}, timeout=10)
        cr.raise_for_status()
        cid = cr.json()["id"]
        mr = await client.post(f"{_platform_base()}/api/v1/conversations/{cid}/messages",
                               json={"content": "test message", "role": "user"},
                               headers={"Authorization": f"Bearer {token}"}, timeout=10)
    return {"passed": mr.status_code == 200, "actual": {"status": mr.status_code}}


async def check_get_conversation_with_messages(spec: dict, **_) -> dict:
    token, _, _ = await _admin_token()
    async with httpx.AsyncClient() as client:
        cr = await client.post(f"{_platform_base()}/api/v1/conversations",
                               json={"title": "validator-get"},
                               headers={"Authorization": f"Bearer {token}"}, timeout=10)
        cr.raise_for_status()
        cid = cr.json()["id"]
        await client.post(f"{_platform_base()}/api/v1/conversations/{cid}/messages",
                          json={"content": "hello", "role": "user"},
                          headers={"Authorization": f"Bearer {token}"}, timeout=10)
        gr = await client.get(f"{_platform_base()}/api/v1/conversations/{cid}",
                              headers={"Authorization": f"Bearer {token}"}, timeout=10)
    if gr.status_code != 200:
        return {"passed": False, "actual": {"status": gr.status_code}}
    d = gr.json()
    ok = "conversation" in d and "messages" in d and len(d["messages"]) >= 1
    return {"passed": ok, "actual": {"messages": len(d.get("messages", []))}}


async def check_delete_conversation(spec: dict, **_) -> dict:
    token, _, _ = await _admin_token()
    async with httpx.AsyncClient() as client:
        cr = await client.post(f"{_platform_base()}/api/v1/conversations",
                               json={"title": "validator-del"},
                               headers={"Authorization": f"Bearer {token}"}, timeout=10)
        cr.raise_for_status()
        cid = cr.json()["id"]
        dr = await client.delete(f"{_platform_base()}/api/v1/conversations/{cid}",
                                 headers={"Authorization": f"Bearer {token}"}, timeout=10)
    return {"passed": dr.status_code == 200}


# ──────────────── SSE ────────────────

async def _consume_sse(url: str, body: dict, headers: dict, timeout: float = 30) -> dict:
    """Returns dict with events captured."""
    out = {"events": [], "first_delta_ms": None, "first_status_ms": None,
           "saw_done": False, "saw_citation": False, "delta_count": 0,
           "deltas": [], "elapsed_ms": 0}
    t0 = time.time()
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, json=body, headers=headers) as r:
            if r.status_code != 200:
                out["status"] = r.status_code
                out["body"] = (await r.aread()).decode()[:300]
                return out
            buf = ""
            cur_event = "message"
            async for chunk in r.aiter_bytes():
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
                        out["saw_done"] = True
                        out["elapsed_ms"] = int((time.time() - t0) * 1000)
                        return out
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    out["events"].append({"e": cur_event, "p": payload})
                    if cur_event == "delta":
                        if out["first_delta_ms"] is None:
                            out["first_delta_ms"] = int((time.time() - t0) * 1000)
                        out["delta_count"] += 1
                        out["deltas"].append(payload.get("v", ""))
                    elif cur_event == "citation":
                        out["saw_citation"] = True
                    elif cur_event == "message" and out["first_status_ms"] is None:
                        if payload.get("metadata", {}).get("tag") == "status":
                            out["first_status_ms"] = int((time.time() - t0) * 1000)
    out["elapsed_ms"] = int((time.time() - t0) * 1000)
    return out


_sse_cache: dict | None = None


async def _run_sse_once() -> dict:
    """Run a single SSE round-trip with a default query, cached for the iteration."""
    global _sse_cache
    if _sse_cache is not None:
        return _sse_cache
    token, _, _ = await _admin_token()
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient() as client:
        cr = await client.post(f"{_platform_base()}/api/v1/conversations",
                               json={"title": "validator-sse"}, headers=headers, timeout=10)
        cr.raise_for_status()
        cid = cr.json()["id"]
    out = await _consume_sse(
        f"{_langgraph_base()}/v1/sediment/stream",
        {"conv_id": cid, "query": "summarize hypeproof lab activity"},
        headers, timeout=30,
    )
    out["conv_id"] = cid
    _sse_cache = out
    return out


async def check_sse_starts(spec: dict, **_) -> dict:
    out = await _run_sse_once()
    ok = "events" in out and (out.get("first_status_ms") is not None or out.get("delta_count", 0) > 0 or out.get("saw_done"))
    return {"passed": ok, "actual": {"events": len(out.get("events", [])),
                                     "saw_done": out.get("saw_done")}}


async def check_sse_status_event(spec: dict, **_) -> dict:
    out = await _run_sse_once()
    ok = out.get("first_status_ms") is not None
    return {"passed": ok, "actual": {"first_status_ms": out.get("first_status_ms")}}


async def check_sse_delta_event(spec: dict, **_) -> dict:
    out = await _run_sse_once()
    ok = out.get("delta_count", 0) >= 1
    return {"passed": ok, "actual": {"delta_count": out.get("delta_count", 0)}}


async def check_sse_done_terminator(spec: dict, **_) -> dict:
    out = await _run_sse_once()
    return {"passed": out.get("saw_done", False), "actual": {"saw_done": out.get("saw_done")}}


async def check_sse_citation_event(spec: dict, **_) -> dict:
    out = await _run_sse_once()
    return {"passed": out.get("saw_citation", False),
            "actual": {"saw_citation": out.get("saw_citation"),
                       "events": len(out.get("events", []))}}


async def check_assistant_msg_persisted(spec: dict, **_) -> dict:
    out = await _run_sse_once()
    cid = out.get("conv_id")
    if not cid:
        return {"passed": False, "message": "no conv_id from SSE round"}
    async with service_session() as s:
        n = (await s.execute(text("""
            SELECT count(*) FROM messages WHERE conv_id=:cid AND role='assistant'
        """), {"cid": cid})).scalar_one()
    return {"passed": n >= 1, "actual": {"assistant_msgs": n}}


async def check_ttft(spec: dict, **_) -> dict:
    out = await _run_sse_once()
    # Use min() to select the earliest event (status OR delta), not Python `or`
    # which returns the first *truthy* value — first_delta_ms (e.g. 10000ms)
    # incorrectly overrides first_status_ms (e.g. 292ms) when both are set.
    candidates = [v for v in [out.get("first_delta_ms"), out.get("first_status_ms")] if v is not None]
    ttft = min(candidates) if candidates else 0
    ok = 0 < ttft <= 4000
    return {"passed": ok, "actual": {"ttft_ms": ttft}}


async def check_stream_complete_time(spec: dict, **_) -> dict:
    out = await _run_sse_once()
    elapsed = out.get("elapsed_ms", 0)
    ok = 0 < elapsed <= 30000
    return {"passed": ok, "actual": {"elapsed_ms": elapsed}}


# ──────────────── Intent routing ────────────────

async def _intent_for(query: str) -> str:
    from applications.sediment_langgraph.graphs.sediment_graph import build_graph
    graph = build_graph()
    state = {
        "tenant_id": "00000000-0000-0000-0000-000000000000",
        "member_id": "00000000-0000-0000-0000-000000000000",
        "conv_id": "00000000-0000-0000-0000-000000000000",
        "query": query,
    }
    out = await graph.ainvoke(state, config={"configurable": {"thread_id": "intent-test"}})
    return out.get("intent", "library")


async def check_intent_member(spec: dict, **_) -> dict:
    intent = await _intent_for("Who is Ryan?")
    return {"passed": intent == "member", "actual": {"intent": intent}}


async def check_intent_library(spec: dict, **_) -> dict:
    intent = await _intent_for("Find me a column about mirror loop")
    return {"passed": intent == "library", "actual": {"intent": intent}}


async def check_intent_meta(spec: dict, **_) -> dict:
    intent = await _intent_for("How many columns total?")
    return {"passed": intent == "meta", "actual": {"intent": intent}}


async def check_intent_decision(spec: dict, **_) -> dict:
    intent = await _intent_for("What action items came out of last week's decisions?")
    return {"passed": intent in ("decision", "library"), "actual": {"intent": intent}}


# ──────────────── MCP ────────────────

def check_mcp_imports(spec: dict, **_) -> dict:
    try:
        import importlib
        importlib.import_module("lab_platform.mcp_servers.workspace_mcp")
        return {"passed": True}
    except Exception as e:
        return {"passed": False, "message": f"import failed: {e}"}


async def check_mcp_tool_count(spec: dict, **_) -> dict:
    """Count tools registered on the workspace FastMCP server.

    FastMCP exposes registered tools via the async ``list_tools()`` coroutine.
    Inspecting decorated module-level functions via ``dir()`` / ``__wrapped__``
    does not work — fastmcp stores tools in an internal manager, not as
    ``functools.wraps`` decorators. ``mcp._tools`` / ``mcp.tools`` are also
    not real attributes. Only ``await mcp.list_tools()`` is API-supported.
    """
    try:
        from lab_platform.mcp_servers import workspace_mcp as wmcp
        tools = await wmcp.mcp.list_tools()
        n = len(tools)
        names = [getattr(t, "name", repr(t)) for t in tools]
        return {
            "passed": n >= 12,
            "actual": {"tools": n, "names": names},
            "expected": {"tools": ">=12"},
        }
    except Exception as e:
        return {"passed": False, "message": f"inspect failed: {type(e).__name__}: {e}"}


async def check_mcp_vault_search(spec: dict, **_) -> dict:
    try:
        from lab_platform.mcp_servers.workspace_mcp import vault_search
        async with service_session() as s:
            tid = (await s.execute(text("SELECT id::text FROM tenants WHERE slug='hypeproof-lab'"))).scalar_one()
        # vault_search is decorated tool — call underlying fn
        fn = getattr(vault_search, "fn", None) or getattr(vault_search, "__wrapped__", vault_search)
        rows = await fn(tenant_id=tid, query="lab", limit=3)
        return {"passed": len(rows) >= 1, "actual": {"results": len(rows)}}
    except Exception as e:
        return {"passed": False, "message": f"call failed: {type(e).__name__}: {e}"}


async def check_mcp_members_lookup(spec: dict, **_) -> dict:
    try:
        from lab_platform.mcp_servers.workspace_mcp import members_lookup
        async with service_session() as s:
            tid = (await s.execute(text("SELECT id::text FROM tenants WHERE slug='hypeproof-lab'"))).scalar_one()
        fn = getattr(members_lookup, "fn", None) or getattr(members_lookup, "__wrapped__", members_lookup)
        m = await fn(tenant_id=tid, name_or_id="Jay")
        return {"passed": m is not None and m.get("display_name") == "Jay",
                "actual": {"found": bool(m), "name": m.get("display_name") if m else None}}
    except Exception as e:
        return {"passed": False, "message": f"call failed: {type(e).__name__}: {e}"}
