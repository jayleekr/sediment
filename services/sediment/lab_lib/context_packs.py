"""Context packs — cheap, pre-built context a session can read unconditionally.

sediment#142.

A wiki keeps a ~500-word `hot.md` and a full `index.md` so any session picks up
where the last one left off without crawling. Sediment had no equivalent: a new
session either ran a vector search — which answers a question it does not yet
know to ask — or started blind.

Two properties make a pack worth having, and both are constraints, not features:

**It must be cheap enough to read every time.** A hot pack over its budget has
stopped being a cache and become another document to search. ``HOT_TOKEN_BUDGET``
is enforced by truncation, not by hoping the query returns little.

**It must respect who is asking.** The file version assumes one owner. Here a
``member:<uuid>`` pack is built through #140's visibility predicate, so it can
never surface a page that member could not open directly. A pack is a
disclosure path exactly like a derived page is.

Packs are derived data: losing them costs a regeneration, not knowledge.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text

from lab_lib.visibility import visibility_filter_sql

#: Roughly 500 words. The number that matters is that a session can afford this
#: on every startup without thinking about it.
HOT_TOKEN_BUDGET = 700
INDEX_TOKEN_BUDGET = 1500

#: ~4 chars/token for mixed Korean/English prose. Deliberately crude — this
#: guards a budget, it does not bill anyone.
_CHARS_PER_TOKEN = 4


def estimate_tokens(s: str) -> int:
    return max(1, len(s) // _CHARS_PER_TOKEN)


def _truncate_to_budget(body: str, budget: int) -> str:
    """Cut to the last complete line that fits. A pack that silently blew its
    budget is worse than a short one — it is the failure the budget exists to
    prevent, and mid-sentence truncation hides it."""
    if estimate_tokens(body) <= budget:
        return body
    keep = budget * _CHARS_PER_TOKEN
    cut = body[:keep]
    nl = cut.rfind("\n")
    if nl > 0:
        cut = cut[:nl]
    return cut.rstrip() + "\n\n_(truncated to fit the context-pack budget)_\n"


@dataclass(frozen=True)
class Pack:
    scope_key: str
    kind: str
    body: str
    token_estimate: int
    sources: dict


def parse_scope(scope_key: str) -> tuple[str, Optional[str]]:
    """('tenant', None) | ('member', uuid) | ('domain', slug)."""
    if scope_key == "tenant":
        return "tenant", None
    prefix, _, value = scope_key.partition(":")
    if prefix in ("member", "domain") and value:
        return prefix, value
    raise ValueError(
        f"unknown scope_key {scope_key!r}; expected 'tenant', 'member:<uuid>' "
        "or 'domain:<slug>'"
    )


async def build_hot_pack(session, tenant_id: str, scope_key: str = "tenant") -> Pack:
    """Recent context: what changed, what was decided, what is disputed.

    The viewer for the visibility predicate comes from the SCOPE, not from
    whoever triggered the rebuild — a pack is stored and re-read later by the
    member it belongs to, so building it with the trigger's permissions would
    leak across members on the next read.
    """
    kind, value = parse_scope(scope_key)
    viewer = value if kind == "member" else ""
    vis = visibility_filter_sql("a")
    params = {"tid": str(tenant_id), "viewer_member_id": viewer}

    recent = await session.execute(text(f"""
        SELECT a.ref, a.type, a.origin, a.updated_at
        FROM artifacts a
        WHERE a.tenant_id = CAST(:tid AS uuid) AND {vis}
        ORDER BY a.updated_at DESC
        LIMIT 12
    """), params)
    recent_rows = [dict(r._mapping) for r in recent]

    decisions = await session.execute(text(f"""
        SELECT a.ref, a.frontmatter ->> 'topic' AS topic, a.updated_at
        FROM artifacts a
        WHERE a.tenant_id = CAST(:tid AS uuid) AND a.type = 'decision' AND {vis}
        ORDER BY a.updated_at DESC
        LIMIT 6
    """), params)
    decision_rows = [dict(r._mapping) for r in decisions]

    # Open contradictions lead the pack. An unresolved conflict is the single
    # most useful thing a session can know before it starts answering, and the
    # one thing that used to be invisible (sediment#141).
    conflicts = await session.execute(text(f"""
        SELECT src.ref AS src_ref, dst.ref AS dst_ref, l.created_at
        FROM artifact_links l
        JOIN artifacts src ON src.id = l.src_artifact_id
        JOIN artifacts dst ON dst.id = l.dst_artifact_id
        JOIN artifacts a ON a.id = l.src_artifact_id
        WHERE l.tenant_id = CAST(:tid AS uuid)
          AND l.kind = 'contradicts' AND l.resolved_at IS NULL
          AND {vis}
        ORDER BY l.created_at DESC
        LIMIT 5
    """), params)
    conflict_rows = [dict(r._mapping) for r in conflicts]

    lines: list[str] = ["# Recent context", ""]
    if conflict_rows:
        lines.append("## Open contradictions (unresolved)")
        for c in conflict_rows:
            lines.append(f"- `{c['src_ref']}` vs `{c['dst_ref']}`")
        lines.append("")
    if decision_rows:
        lines.append("## Latest decisions")
        for d in decision_rows:
            lines.append(f"- {d['topic'] or d['ref']} (`{d['ref']}`)")
        lines.append("")
    if recent_rows:
        lines.append("## Recently updated")
        for a in recent_rows:
            marker = " *(synthesized)*" if a["origin"] == "derived" else ""
            lines.append(f"- `{a['ref']}` — {a['type']}{marker}")
        lines.append("")
    if not (conflict_rows or decision_rows or recent_rows):
        # Say so rather than returning an empty pack: "nothing here" and "the
        # pack failed to build" must not look the same to a reading session.
        lines.append("_No artifacts visible in this scope yet._")

    body = _truncate_to_budget("\n".join(lines).rstrip() + "\n", HOT_TOKEN_BUDGET)
    return Pack(
        scope_key=scope_key, kind="hot", body=body,
        token_estimate=estimate_tokens(body),
        sources={
            "recent": len(recent_rows),
            "decisions": len(decision_rows),
            "open_contradictions": len(conflict_rows),
        },
    )


async def build_index_pack(session, tenant_id: str, scope_key: str = "tenant") -> Pack:
    """Catalogue: what kinds of things exist here and how much of each."""
    kind, value = parse_scope(scope_key)
    viewer = value if kind == "member" else ""
    vis = visibility_filter_sql("a")
    params = {"tid": str(tenant_id), "viewer_member_id": viewer}

    by_type = await session.execute(text(f"""
        SELECT a.type, a.origin, count(*) AS n, max(a.updated_at) AS latest
        FROM artifacts a
        WHERE a.tenant_id = CAST(:tid AS uuid) AND {vis}
        GROUP BY a.type, a.origin
        ORDER BY n DESC
    """), params)
    rows = [dict(r._mapping) for r in by_type]

    lines = ["# Vault index", ""]
    raw = [r for r in rows if r["origin"] == "raw"]
    derived = [r for r in rows if r["origin"] == "derived"]
    if raw:
        lines.append("## Sources")
        lines += [f"- {r['type']}: {r['n']}" for r in raw]
        lines.append("")
    if derived:
        lines.append("## Synthesized")
        lines += [f"- {r['type']}: {r['n']}" for r in derived]
        lines.append("")
    if not rows:
        lines.append("_No artifacts visible in this scope yet._")

    body = _truncate_to_budget("\n".join(lines).rstrip() + "\n", INDEX_TOKEN_BUDGET)
    return Pack(
        scope_key=scope_key, kind="index", body=body,
        token_estimate=estimate_tokens(body),
        sources={"type_groups": len(rows)},
    )


async def store_pack(session, tenant_id: str, pack: Pack) -> None:
    await session.execute(text("""
        INSERT INTO context_packs
            (tenant_id, scope_key, kind, body, token_estimate, sources, updated_at)
        VALUES (CAST(:tid AS uuid), :scope, :kind, :body, :tokens,
                CAST(:sources AS jsonb), now())
        ON CONFLICT (tenant_id, scope_key, kind) DO UPDATE SET
            body = EXCLUDED.body,
            token_estimate = EXCLUDED.token_estimate,
            sources = EXCLUDED.sources,
            updated_at = now()
    """), {
        "tid": str(tenant_id), "scope": pack.scope_key, "kind": pack.kind,
        "body": pack.body, "tokens": pack.token_estimate,
        "sources": json.dumps(pack.sources),
    })


async def read_pack(session, tenant_id: str, scope_key: str,
                    kind: str) -> Optional[dict]:
    r = await session.execute(text("""
        SELECT scope_key, kind, body, token_estimate, sources, updated_at
        FROM context_packs
        WHERE tenant_id = CAST(:tid AS uuid) AND scope_key = :scope AND kind = :kind
    """), {"tid": str(tenant_id), "scope": scope_key, "kind": kind})
    row = r.first()
    return dict(row._mapping) if row else None


async def rebuild_packs(session, tenant_id: str, scope_key: str = "tenant") -> list[Pack]:
    """Rebuild and store both packs for one scope. Returns what was written.

    Never raises: pack generation runs off the back of ingestion, and a failure
    here must not fail the ingest that triggered it.
    """
    packs: list[Pack] = []
    for builder in (build_hot_pack, build_index_pack):
        try:
            pack = await builder(session, tenant_id, scope_key)
            await store_pack(session, tenant_id, pack)
            packs.append(pack)
        except Exception:
            import logging
            logging.getLogger("lab_lib.context_packs").warning(
                "context_pack.build_failed scope=%s builder=%s",
                scope_key, builder.__name__, exc_info=True)
    return packs
