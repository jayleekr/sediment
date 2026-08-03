"""Workspace MCP Server :8888 (INTERNAL — 127.0.0.1 only).

Tenant-aware tools exposed to the LangGraph agent. Tenant context is
passed by the caller (langgraph) as ``tenant_id`` argument on every call —
FastMCP doesn't propagate session state across tools cleanly, so we go
explicit.

12 tools (matches SPEC §8.2):
  vault_search, vault_read,
  members_lookup, members_expertise_match,
  contributions_summary,
  discord_recent, calendar_search,
  decisions_open, actions_by_owner,
  lens_explain, lens_apply,
  events_recent

============================================================================
TRUST MODEL — read before adding tools or changing the bind address
============================================================================

This server differs from ``applications/sediment_mcp/server.py`` (the
*external* claude.ai-facing MCP shim) in TWO important ways:

  1. **Tenant comes from a tool argument**, not from a JWT. Any caller
     that can reach :8888 can pass any tenant_id and get that tenant's
     data. This is by design — the LangGraph agent is the only intended
     caller, and it already has the tenant from its parent request scope.
  2. **No auth.** There is no bearer token, no API key, no signature.

Both choices are SAFE IF AND ONLY IF the bind address is 127.0.0.1 and
nothing else on the host can reach the port. The startup guard below
enforces this — any future "just bind to 0.0.0.0 for the Studio Worker"
PR is a CRITICAL security regression that turns this into a cross-tenant
data exfiltration endpoint.

If you need a real external MCP surface, use ``sediment_mcp/server.py``
(which derives tenant from the JWT, the correct trust model for that
direction). DO NOT widen workspace_mcp's bind.

WO-7 #1 2026-05-24 — trust model documented + bind guard added. Future
hardening: replace the ``tenant_id`` argument with a short-lived service
JWT (``lab_lib.auth.mint_service_token``) so even a compromised localhost
process can't pivot across tenants without first stealing the JWT secret.
"""
from __future__ import annotations
import asyncio
import json
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy import text

from fastmcp import FastMCP
from lab_lib.db import app_session
from lab_lib.embeddings import embed_one
from lab_lib.logging import configure_logging, get_logger
from lab_lib.search_utils import build_ts_or_query, is_zero_vector
from lab_lib.settings import settings
from lab_lib.visibility import visibility_filter_sql

configure_logging()
log = get_logger("workspace_mcp")

mcp = FastMCP("workspace_mcp")


# ============================================================
# Vault tools
# ============================================================

@mcp.tool()
async def vault_search(
    tenant_id: str,
    query: str,
    type: Optional[str] = None,
    author_external_id: Optional[str] = None,
    lens: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 8,
    viewer_member_id: Optional[str] = None,
) -> list[dict]:
    """Hybrid search (BM25 + vector) over the tenant's vault.
    Returns chunks with citations sorted by RRF rank.

    WO-7 2026-05-23 (REPORT.md HIGH): zero-vector guard added. Previously
    this path called ``embed_one()`` and stuffed the result straight into
    cosine distance — when no OpenAI/Gemini key is set, ``embed_one``
    returns a zero vector that produces NaN under cosine and silently
    yields empty results (the LEARNINGS-class regression that library.py
    and lab_curator_graph.py both guarded against but this file did not).
    Offline path now mirrors the other two: BM25-only with OR-joined
    to_tsquery so short / multi-token queries still return real hits.
    """
    qvec = embed_one(query)
    qvec_zero = is_zero_vector(qvec)
    qvec_str = "[" + ",".join(f"{x:.6f}" for x in qvec) + "]"

    # Build filters.
    # The visibility predicate (sediment#140) joins the shared filter list, so
    # it applies inside the bm25/vec CTEs too — this file's CTEs already join
    # artifacts, unlike the platform /search hybrid path where it can only be
    # applied post-fusion. `viewer_member_id=None` → tenant-visible rows only
    # (fail-closed): an MCP caller that has not identified a member does not
    # get that member's restricted pages.
    filters = ["a.tenant_id = current_tenant_id()", visibility_filter_sql("a")]
    params: dict = {
        "q": query, "qvec": qvec_str, "limit": limit,
        "viewer_member_id": viewer_member_id or "",
    }
    if type:
        filters.append("a.type = :type"); params["type"] = type
    if author_external_id:
        filters.append("EXISTS (SELECT 1 FROM members m WHERE m.id = a.author_id AND m.external_id = :eid)")
        params["eid"] = author_external_id
    if lens:
        filters.append("a.frontmatter -> 'lens' ? :lens")
        params["lens"] = lens
    if date_from:
        filters.append("a.date >= :df"); params["df"] = date_from
    if date_to:
        filters.append("a.date <= :dt"); params["dt"] = date_to
    where = " AND ".join(filters)

    if qvec_zero:
        # Offline mode — no real embeddings. BM25-only with OR-joined
        # to_tsquery so the query still returns useful results.
        ts_or = build_ts_or_query(query)
        if not ts_or:
            return []
        params["ts_or"] = ts_or
        sql = f"""
        SELECT c.id AS chunk_id, c.artifact_id, c.seq, c.content,
               ts_rank(c.tsv, to_tsquery('simple', :ts_or)) AS score,
               a.ref, a.type, a.date, a.slug, a.frontmatter, c.heading_path
        FROM chunks c JOIN artifacts a ON a.id = c.artifact_id
        WHERE {where} AND c.tsv @@ to_tsquery('simple', :ts_or)
        ORDER BY score DESC
        LIMIT :limit;
        """
        async with app_session(tenant_id) as s:
            r = await s.execute(text(sql), params)
            return [dict(row._mapping) for row in r]

    # Online hybrid path — RRF over BM25 + cosine.
    sql = f"""
    WITH bm25 AS (
      SELECT c.id, c.artifact_id, c.seq, c.content,
             ts_rank(c.tsv, plainto_tsquery('simple', :q)) AS score,
             row_number() OVER (ORDER BY ts_rank(c.tsv, plainto_tsquery('simple', :q)) DESC) AS rank
      FROM chunks c JOIN artifacts a ON a.id = c.artifact_id
      WHERE {where} AND c.tsv @@ plainto_tsquery('simple', :q)
      LIMIT 50
    ),
    vec AS (
      SELECT c.id, c.artifact_id, c.seq, c.content,
             1 - (c.embedding <=> CAST(:qvec AS vector)) AS score,
             row_number() OVER (ORDER BY c.embedding <=> CAST(:qvec AS vector)) AS rank
      FROM chunks c JOIN artifacts a ON a.id = c.artifact_id
      WHERE {where}
      ORDER BY c.embedding <=> CAST(:qvec AS vector) LIMIT 50
    ),
    fused AS (
      SELECT id, artifact_id, seq, content, sum(rrf) AS score FROM (
        SELECT id, artifact_id, seq, content, 1.0 / (60 + rank) AS rrf FROM bm25
        UNION ALL
        SELECT id, artifact_id, seq, content, 1.0 / (60 + rank) AS rrf FROM vec
      ) u GROUP BY id, artifact_id, seq, content
    )
    SELECT f.id AS chunk_id, f.artifact_id, f.seq, f.content, f.score,
           a.ref, a.type, a.date, a.slug, a.frontmatter, ch.heading_path
    -- heading_path (sediment#137) joined by PK: f.id IS the chunk id, so this
    -- avoids threading the column through the RRF GROUP BY.
    FROM fused f JOIN artifacts a ON a.id = f.artifact_id
    JOIN chunks ch ON ch.id = f.id
    ORDER BY f.score DESC LIMIT :limit;
    """

    async with app_session(tenant_id) as s:
        r = await s.execute(text(sql), params)
        return [dict(row._mapping) for row in r]


@mcp.tool()
async def vault_read(tenant_id: str, ref: str) -> dict:
    """Read a single artifact by ref."""
    async with app_session(tenant_id) as s:
        r = await s.execute(text("""
            SELECT id, ref, type, date, slug, lang, frontmatter, body, status
            FROM artifacts WHERE ref = :ref LIMIT 1
        """), {"ref": ref})
        row = r.first()
        return dict(row._mapping) if row else {}


# ============================================================
# Members + Contributions
# ============================================================

@mcp.tool()
async def members_lookup(tenant_id: str, name_or_id: str) -> Optional[dict]:
    """Look up a member by display name, real name, email, or external_id."""
    async with app_session(tenant_id) as s:
        r = await s.execute(text("""
            SELECT id, external_id, email, display_name, real_name, role, title,
                   expertise, interests
            FROM members
            WHERE display_name = :q OR real_name = :q OR email = :q OR external_id = :q
            LIMIT 1
        """), {"q": name_or_id})
        row = r.first()
        return dict(row._mapping) if row else None


@mcp.tool()
async def members_expertise_match(tenant_id: str, topic: str, limit: int = 5) -> list[dict]:
    """Find members whose declared expertise contains the topic substring."""
    async with app_session(tenant_id) as s:
        r = await s.execute(text("""
            SELECT id, display_name, real_name, expertise
            FROM members
            WHERE expertise::text ILIKE :p
            LIMIT :limit
        """), {"p": f"%{topic}%", "limit": limit})
        return [dict(row._mapping) for row in r]


@mcp.tool()
async def contributions_summary(tenant_id: str, member_external_id: str,
                                  since_days: int = 30) -> dict:
    """Counts of artifacts authored by a member in the last N days, grouped by type."""
    async with app_session(tenant_id) as s:
        r = await s.execute(text("""
            SELECT a.type, count(*) AS n
            FROM artifacts a JOIN members m ON m.id = a.author_id
            WHERE m.external_id = :eid AND a.date >= now() - (:d || ' days')::interval
            GROUP BY a.type
        """), {"eid": member_external_id, "d": str(since_days)})
        rows = [dict(row._mapping) for row in r]
        total = sum(x["n"] for x in rows)
    return {"member_external_id": member_external_id, "since_days": since_days,
            "total": total, "by_type": rows}


# ============================================================
# Decisions + Actions
# ============================================================

@mcp.tool()
async def decisions_open(tenant_id: str, topic: Optional[str] = None,
                          limit: int = 20) -> list[dict]:
    """Open decisions, optionally filtered by topic substring."""
    async with app_session(tenant_id) as s:
        if topic:
            r = await s.execute(text("""
                SELECT id, topic, body, status, made_at, created_at
                FROM decisions WHERE status = 'open' AND topic ILIKE :p
                ORDER BY created_at DESC LIMIT :limit
            """), {"p": f"%{topic}%", "limit": limit})
        else:
            r = await s.execute(text("""
                SELECT id, topic, body, status, made_at, created_at
                FROM decisions WHERE status = 'open'
                ORDER BY created_at DESC LIMIT :limit
            """), {"limit": limit})
        return [dict(row._mapping) for row in r]


@mcp.tool()
async def actions_by_owner(tenant_id: str, member_external_id: str,
                             status: str = "open") -> list[dict]:
    """Actions assigned to a member."""
    async with app_session(tenant_id) as s:
        r = await s.execute(text("""
            SELECT a.id, a.description, a.due_date, a.status, d.topic AS decision_topic
            FROM actions a
            JOIN members m ON m.id = a.owner_id
            LEFT JOIN decisions d ON d.id = a.decision_id
            WHERE m.external_id = :eid AND a.status = :s
            ORDER BY a.due_date NULLS LAST, a.created_at DESC
        """), {"eid": member_external_id, "s": status})
        return [dict(row._mapping) for row in r]


# ============================================================
# Events + signals
# ============================================================

@mcp.tool()
async def events_recent(tenant_id: str, source: Optional[str] = None,
                         hours: int = 24, limit: int = 50) -> list[dict]:
    """Recent raw events from Discord/cron/web/etc."""
    async with app_session(tenant_id) as s:
        if source:
            r = await s.execute(text("""
                SELECT id, source, kind, payload, ts FROM events
                WHERE source = :src AND ts >= now() - (:h || ' hours')::interval
                ORDER BY ts DESC LIMIT :limit
            """), {"src": source, "h": str(hours), "limit": limit})
        else:
            r = await s.execute(text("""
                SELECT id, source, kind, payload, ts FROM events
                WHERE ts >= now() - (:h || ' hours')::interval
                ORDER BY ts DESC LIMIT :limit
            """), {"h": str(hours), "limit": limit})
        return [dict(row._mapping) for row in r]


@mcp.tool()
async def discord_recent(tenant_id: str, channel: Optional[str] = None,
                          hours: int = 12, limit: int = 30) -> list[dict]:
    """Recent Discord-source events (filtered through events table)."""
    async with app_session(tenant_id) as s:
        sql = """
            SELECT id, kind, payload, ts FROM events
            WHERE source = 'discord' AND ts >= now() - (:h || ' hours')::interval
        """
        params = {"h": str(hours), "limit": limit}
        if channel:
            sql += " AND payload ->> 'channel' = :ch"
            params["ch"] = channel
        sql += " ORDER BY ts DESC LIMIT :limit"
        r = await s.execute(text(sql), params)
        return [dict(row._mapping) for row in r]


@mcp.tool()
async def calendar_search(tenant_id: str, query: str, limit: int = 10) -> list[dict]:
    """Search calendar events stored in events.payload (kind='calendar.event'). Stub for now."""
    async with app_session(tenant_id) as s:
        r = await s.execute(text("""
            SELECT id, payload, ts FROM events
            WHERE source = 'web' AND kind = 'calendar.event'
              AND payload::text ILIKE :p
            ORDER BY ts DESC LIMIT :limit
        """), {"p": f"%{query}%", "limit": limit})
        return [dict(row._mapping) for row in r]


# ============================================================
# Lens (philosophy)
# ============================================================

LENSES: dict[str, str] = {
    "mirror-loop": "Output of a system feeds back as its own input — agents that watch and edit themselves.",
    "doing-is-learning": "Skills crystallize from acts, not docs. Memory of what worked beats theories of what should.",
    "initiative-gap": "The gulf between people who wait for prompts and people who name problems unprompted.",
    "margin-eraser": "Tools that wipe the differentiator middle classes once paid for. Cheaper, identical output.",
    "vanilla-wins": "Boring, well-known stacks beat clever ones because the team already trusts them.",
    "re-bundle": "Disaggregated atoms get reassembled into wholes — but with new glue and new boundaries.",
    "humanities-hypothesis": "The bottleneck has shifted from technique to taste, from execution to interpretation.",
}


@mcp.tool()
async def lens_explain(lens_id: str) -> dict:
    """Return the canonical description of one of HypeProof's 7 philosophical lenses."""
    desc = LENSES.get(lens_id)
    if not desc:
        return {"error": f"unknown lens: {lens_id}", "available": list(LENSES.keys())}
    return {"id": lens_id, "description": desc}


@mcp.tool()
async def lens_apply(text_to_reframe: str, lens_id: str) -> dict:
    """Reframe a text through a chosen lens. Returns prompt-ready instruction.

    The actual rewriting is done by the LLM consuming this output —
    this tool returns the lens definition + a re-framing template.
    """
    desc = LENSES.get(lens_id)
    if not desc:
        return {"error": f"unknown lens: {lens_id}"}
    return {
        "lens": lens_id,
        "definition": desc,
        "reframe_instruction": (
            f"Reframe the following text through the '{lens_id}' lens. "
            f"The lens means: {desc} "
            "Identify what changes in interpretation. Output: 1) the reframed claim in 2 sentences, "
            "2) one specific evidence point from the source that supports the new framing."
        ),
        "source_text": text_to_reframe,
    }


# ============================================================
# Entrypoint
# ============================================================

if __name__ == "__main__":
    # WO-7 #1 2026-05-24: hard-coded loopback bind. The trust model above
    # depends on this — see the module docstring. Reading from an env var
    # would let a deploy config drift the bind to 0.0.0.0 silently.
    host = "127.0.0.1"
    # Belt-and-suspenders: refuse any attempt to override via env vars
    # someone might add later. WORKSPACE_MCP_HOST is intentionally not read.
    import os as _os
    forbidden = _os.environ.get("WORKSPACE_MCP_HOST")
    if forbidden and forbidden != host:
        raise SystemExit(
            f"FATAL: WORKSPACE_MCP_HOST={forbidden!r} ignored — this server "
            f"must bind to 127.0.0.1 only (see module docstring trust model). "
            f"To override, edit workspace_mcp.py directly + remove the no-auth "
            f"tool surface FIRST."
        )
    log.info("workspace_mcp.start", host=host, port=settings.workspace_mcp_port)
    # FastMCP supports stdio + http transports. We run http for langgraph to consume.
    mcp.run(transport="http", host=host, port=settings.workspace_mcp_port)
