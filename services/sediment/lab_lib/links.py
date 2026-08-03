"""Artifact link graph — writing links, and using them to widen retrieval.

sediment#141.

Two things the graph buys, and one rule about how it may be used.

**Conflict becomes an asset.** #138 stopped a colliding re-distill from erasing
the previous body, but a second author's differing decision still had nowhere to
exist as its own claim. Here it gets a sibling page and a `contradicts` link,
open until somebody adjudicates it. In a single-author vault a conflict is a
mistake to fix; across people it is the most valuable signal the system has.

**Retrieval can follow references.** BM25 and vector search only find what the
query matched textually. A decision page that cites a spec does not necessarily
repeat the spec's vocabulary.

The rule: expansion may only FILL UNUSED SLOTS. It never displaces a hit the
base retrieval ranked, so it cannot regress precision on the results a user
already gets — it can only add where there was nothing. That constraint is what
makes this safe to ship without a recall benchmark; a fused/re-ranked expansion
is a bigger change and needs measurement first.
"""
from __future__ import annotations

import json
from typing import Iterable, Optional, Sequence

from sqlalchemy import text

#: Kinds worth traversing when widening a result set. `contradicts` is included
#: deliberately: if a cited decision is disputed, the answer should be able to
#: see the dispute. `mentions` is excluded — it is the weakest edge and the
#: noisiest to follow.
EXPANDABLE_KINDS: tuple[str, ...] = ("derived_from", "supports", "supersedes", "contradicts")


async def create_link(session, tenant_id: str, src_artifact_id: str,
                      dst_artifact_id: str, kind: str, *,
                      evidence_chunk_ids: Optional[Sequence[str]] = None,
                      note: Optional[str] = None,
                      created_by: Optional[str] = None) -> Optional[str]:
    """Assert one link. Idempotent; returns the link id, or None if it existed.

    Self-links are rejected before reaching the CHECK constraint — a batch
    writer producing one should get a clear failure, not a constraint error
    buried in a transaction rollback.
    """
    if str(src_artifact_id) == str(dst_artifact_id):
        raise ValueError(f"refusing self-link on artifact {src_artifact_id}")
    r = await session.execute(text("""
        INSERT INTO artifact_links
            (tenant_id, src_artifact_id, dst_artifact_id, kind,
             evidence_chunk_ids, note, created_by)
        VALUES (CAST(:tid AS uuid), CAST(:src AS uuid), CAST(:dst AS uuid), :kind,
                CAST(:evidence AS jsonb), :note,
                CAST(NULLIF(:created_by, '') AS uuid))
        ON CONFLICT (src_artifact_id, dst_artifact_id, kind) DO NOTHING
        RETURNING id::text
    """), {
        "tid": str(tenant_id), "src": str(src_artifact_id),
        "dst": str(dst_artifact_id), "kind": kind,
        "evidence": json.dumps(list(evidence_chunk_ids or [])),
        "note": note, "created_by": str(created_by or ""),
    })
    row = r.first()
    return row[0] if row else None


async def expand_with_links(session, tenant_id: str, items: list[dict], *,
                            limit: int, visibility_sql: str,
                            viewer_member_id: str,
                            kinds: Iterable[str] = EXPANDABLE_KINDS) -> list[dict]:
    """Append 1-hop neighbours of ``items`` until ``limit`` is reached.

    Returns ``items`` unchanged when it is already full — the common case, and
    the reason this costs nothing on a normal query. Neighbours are appended in
    link-recency order with a score of 0.0 so they always sort below real hits,
    and artifacts already present are skipped.

    Never raises: a cluster without migration 008 loses expansion, not results.
    """
    if len(items) >= limit or not items:
        return items

    seen_artifacts = {str(i.get("artifact_id")) for i in items if i.get("artifact_id")}
    if not seen_artifacts:
        return items

    want = limit - len(items)
    kind_list = list(kinds)
    if not kind_list:
        return items

    # Seeds and kinds are expanded into individually-bound placeholders rather
    # than array parameters. Both lists are short, and asyncpg inferring a
    # text[]/uuid[] type through a CAST inside a JOIN condition is exactly the
    # kind of thing that fails at runtime rather than at review time.
    seed_list = sorted(seen_artifacts)
    seed_binds = ", ".join(f"CAST(:seed_{i} AS uuid)" for i in range(len(seed_list)))
    kind_binds = ", ".join(f":kind_{i}" for i in range(len(kind_list)))
    params = {
        "tid": str(tenant_id),
        "want": want,
        "viewer_member_id": viewer_member_id,
        **{f"seed_{i}": s for i, s in enumerate(seed_list)},
        **{f"kind_{i}": k for i, k in enumerate(kind_list)},
    }

    try:
        r = await session.execute(text("""
            SELECT DISTINCT ON (a.id)
                   a.id::text  AS artifact_id,
                   a.ref, a.type, a.date, a.slug, a.origin,
                   l.kind      AS link_kind,
                   (l.kind = 'contradicts' AND l.resolved_at IS NULL) AS link_open_conflict,
                   -- First chunk stands in for the neighbour's content: this is
                   -- a pointer into the graph, not a ranked text match, so
                   -- picking the highest-scoring chunk would imply a relevance
                   -- computation that did not happen.
                   c.id::text  AS chunk_id,
                   c.seq, c.content
            FROM artifact_links l
            JOIN artifacts a
              ON a.id = CASE WHEN l.src_artifact_id IN ({seeds})
                             THEN l.dst_artifact_id ELSE l.src_artifact_id END
            LEFT JOIN chunks c ON c.artifact_id = a.id AND c.seq = 0
            WHERE l.tenant_id = CAST(:tid AS uuid)
              AND a.tenant_id = CAST(:tid AS uuid)
              AND l.kind IN ({kinds})
              AND (l.src_artifact_id IN ({seeds}) OR l.dst_artifact_id IN ({seeds}))
              AND a.id NOT IN ({seeds})
              AND {visibility}
            ORDER BY a.id, l.created_at DESC
            LIMIT :want
        """.replace("{seeds}", seed_binds)
           .replace("{kinds}", kind_binds)
           .replace("{visibility}", visibility_sql)), params)
        neighbours = [dict(row._mapping) for row in r]
    except Exception:
        import logging
        logging.getLogger("lab_lib.links").warning(
            "link_expansion.failed — returning base results", exc_info=True)
        return items

    for n in neighbours:
        if str(n["artifact_id"]) in seen_artifacts:
            continue
        seen_artifacts.add(str(n["artifact_id"]))
        # score 0.0 keeps expansions strictly below every scored hit; via_link
        # lets a caller (and the UI) tell a graph neighbour from a text match.
        n["score"] = 0.0
        n["via_link"] = n.pop("link_kind", None)
        items.append(n)

    return items
