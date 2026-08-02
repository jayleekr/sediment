"""Library — vault browser + search.

sediment#139: this module used to carry verbatim copies of _STOP_WORDS,
_KO_PARTICLE_SUFFIXES, _build_ts_or_query, _prefer_bm25_first and _slug_regex,
even though WO-7 (2026-05-23) had already extracted them to lab_lib.search_utils
precisely so they would stop drifting. They are now imported. The two keyword
boost maps that also lived here are gone entirely — they were one tenant's
vocabulary and now live in `tenant_aliases` (lab_lib.aliases).
"""
from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from lab_lib.aliases import demote_case_sql, load_alias_index
from lab_lib.auth import Identity, require_identity
from lab_lib.db import app_session
from lab_lib.embeddings import embed_one
from lab_lib.logging import get_logger
from lab_lib.search_utils import (
    build_ts_or_query as _build_ts_or_query,
    prefer_bm25_first as _prefer_bm25_first,
    slug_regex as _slug_regex,
)

log = get_logger("sediment.platform.library")

router = APIRouter()


def _embed_for_search(q: str, *, bm25_first: bool) -> list[float]:
    if bm25_first:
        return [0.0]
    try:
        return embed_one(q)
    except Exception as exc:
        # Search should degrade to BM25 when the embedding provider is down,
        # quota-exhausted, or otherwise unavailable. Raising here turns recall
        # quality drift into HTTP 500s and makes the Library UI unusable.
        log.warning("library.search.embed_failed_bm25_fallback", query=q, error=str(exc)[:300])
        return [0.0]


@router.get("")
async def browse(
    type: Optional[str] = None,
    author_external_id: Optional[str] = None,
    lens: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    include_test: bool = Query(default=False, alias="include_test"),
    identity: Identity = Depends(require_identity),
):
    sql = """
        SELECT a.id::text, a.ref, a.type, a.date, a.slug, a.lang, a.frontmatter,
               m.display_name AS author_name, m.external_id AS author_external_id
        FROM artifacts a LEFT JOIN members m ON m.id = a.author_id
        WHERE 1=1
    """
    params: dict = {"limit": limit, "offset": offset}
    if not include_test:
        sql += " AND NOT (a.ref ~ '^validator/(idem|sample)-')"
    if type:
        sql += " AND a.type = :type"
        params["type"] = type
    if author_external_id:
        sql += " AND m.external_id = :eid"
        params["eid"] = author_external_id
    if lens:
        sql += " AND a.frontmatter -> 'lens' ? :lens"
        params["lens"] = lens
    # asyncpg infers a.date's column type as DATE and refuses raw strings —
    # parse to datetime.date here so the bind is type-correct.
    if date_from:
        from datetime import date as _date
        try:
            params["df"] = _date.fromisoformat(date_from)
        except ValueError:
            raise HTTPException(status_code=400,
                                detail=f"date_from must be YYYY-MM-DD, got {date_from!r}")
        sql += " AND a.date >= :df"
    if date_to:
        from datetime import date as _date
        try:
            params["dt"] = _date.fromisoformat(date_to)
        except ValueError:
            raise HTTPException(status_code=400,
                                detail=f"date_to must be YYYY-MM-DD, got {date_to!r}")
        sql += " AND a.date <= :dt"
    sql += " ORDER BY a.date DESC NULLS LAST, a.id LIMIT :limit OFFSET :offset"

    async with app_session(identity.tenant_id) as s:
        r = await s.execute(text(sql), params)
        return {"items": [dict(row._mapping) for row in r]}


@router.get("/search")
async def search(q: str, limit: int = 8, type: Optional[str] = None,
                 identity: Identity = Depends(require_identity)):
    """Hybrid BM25 + vector search. Convenience wrapper for the UI.

    Offline-mode fallback: when the embedding API is unavailable (no
    OPENAI_API_KEY), embed_one returns a zero-vector. pgvector's `<=>` against
    a zero-vector yields NaN, so the vec branch contributes nothing AND the
    BM25 branch alone uses AND-joined plainto_tsquery (too strict for short
    knowledge queries). Detect zero-vector and switch to BM25-only with
    OR-joined to_tsquery — mirrors lab_curator_graph.node_library_search.
    """
    bm25_first = _prefer_bm25_first(q)
    qvec = _embed_for_search(q, bm25_first=bm25_first)
    qvec_is_zero = bm25_first or not any(abs(x) > 1e-9 for x in qvec)

    async with app_session(identity.tenant_id) as s:
        await s.execute(text("SET LOCAL statement_timeout = '8s'"))
        if qvec_is_zero:
            # Offline path: BM25 only, OR-joined for permissive matching.
            ts_or = _build_ts_or_query(q)
            if not ts_or:
                return {"q": q, "items": []}
            # Pass "" when no type hint — avoids asyncpg AmbiguousParameterError
            # on NULL parameters (asyncpg can't infer type from CASE expression).
            # sediment#139: which TERMS match is now the tenant's data
            # (tenant_aliases); the multipliers below stay in code because they
            # are retrieval tuning, not tenant vocabulary. A tenant with no
            # aliases gets no boosts — plain BM25 — instead of another
            # tenant's proper nouns.
            aliases = await load_alias_index(s, str(identity.tenant_id))
            type_hint = aliases.detect_type(q) or ""
            project_hint = aliases.detect_ref_prefix(q)
            slug_re = _slug_regex(q)
            demote_sql, demote_params = demote_case_sql(aliases, "a")
            # Boost stack (multiplicative on ts_rank):
            #   type-boost 3x      — artifact.type matches implied type
            #   project-boost 2x   — ref contains a tenant ref_prefix alias
            #   filename-boost 2x  — query token appears in ref or slug
            #   meta-doc demotion  — tenant's demote_ref_prefix rows, 0.8x
            # Per-artifact dedup keeps the highest-scoring chunk per artifact so
            # one long doc can't dominate top-3.
            # CAST(:hint AS text) forces parameter type inference for asyncpg.
            sql_bm25 = """
            WITH raw AS (
                SELECT c.id::text AS chunk_id, c.artifact_id::text, c.seq, c.content,
                       ts_rank(c.tsv, to_tsquery('simple', :tsq))::float
                           * CASE WHEN CAST(:type_hint AS text) != ''
                                       AND a.type = CAST(:type_hint AS text)
                                  THEN 3.0 ELSE 1.0 END
                           * CASE WHEN CAST(:project_hint AS text) != ''
                                       AND a.ref ILIKE '%' || CAST(:project_hint AS text) || '%'
                                  THEN 2.0 ELSE 1.0 END
                           * CASE WHEN CAST(:slug_re AS text) != '___NEVER___'
                                       AND (LOWER(a.ref) ~ CAST(:slug_re AS text)
                                            OR LOWER(COALESCE(a.slug,'')) ~ CAST(:slug_re AS text))
                                  THEN 2.0 ELSE 1.0 END
                           -- Soft 0.8x meta-doc demotion (was 0.5x → broke
                           -- GQ-031/GQ-033 which legitimately wanted SPEC.md).
                           -- The boosts above carry most of the work now.
                           -- Prefixes come from the tenant's demote_ref_prefix
                           -- aliases; collapses to the constant 1.0 when a
                           -- tenant has configured none.
                           * {demote} AS score,
                       a.ref, a.type, a.date, a.slug
                FROM chunks c JOIN artifacts a ON a.id = c.artifact_id
                WHERE c.tsv @@ to_tsquery('simple', :tsq)
                  AND c.tenant_id = CAST(:tid AS uuid)
                  AND a.tenant_id = CAST(:tid AS uuid)
            ),
            deduped AS (
                SELECT DISTINCT ON (artifact_id)
                       chunk_id, artifact_id, seq, content, score, ref, type, date, slug
                FROM raw
                ORDER BY artifact_id, score DESC
            )
            SELECT chunk_id, artifact_id, seq, content, score, ref, type, date, slug
            FROM deduped
            {type_filter}
            ORDER BY score DESC LIMIT :limit;
            """.replace(
                "{demote}", demote_sql
            ).replace(
                "{type_filter}",
                "WHERE type = :type_strict" if type else ""
            )
            params_bm25 = {
                **demote_params,
                "tsq": ts_or, "limit": limit, "type_hint": type_hint,
                "project_hint": project_hint, "slug_re": slug_re,
                "tid": str(identity.tenant_id),
            }
            if type:
                params_bm25["type_strict"] = type
            r = await s.execute(text(sql_bm25), params_bm25)
            return {"q": q, "items": [dict(row._mapping) for row in r]}

        # Online path: hybrid BM25 + vector with RRF rerank. Keep the
        # ORDER BY/LIMIT work inside *_top CTEs and assign row_number only
        # after truncation, matching the LangGraph retrieval P0 perf fix.
        ts_or = _build_ts_or_query(q)
        qvec_str = "[" + ",".join(f"{x:.6f}" for x in qvec) + "]"
        sql = """
        WITH bm25_top AS (
          SELECT c.id, c.artifact_id, c.seq, c.content,
                 ts_rank(c.tsv, to_tsquery('simple', :tsq))::float AS score
          FROM chunks c
          WHERE CAST(:tsq AS text) <> ''
            AND c.tsv @@ to_tsquery('simple', :tsq)
            AND c.tenant_id = CAST(:tid AS uuid)
          ORDER BY score DESC
          LIMIT 50
        ),
        bm25 AS (
          SELECT id, artifact_id, seq, content,
                 row_number() OVER (ORDER BY score DESC) AS rank
          FROM bm25_top
        ),
        vec_top AS (
          SELECT c.id, c.artifact_id, c.seq, c.content,
                 c.embedding <=> CAST(:qvec AS vector) AS dist
          FROM chunks c
          WHERE c.tenant_id = CAST(:tid AS uuid)
          ORDER BY dist
          LIMIT 50
        ),
        vec AS (
          SELECT id, artifact_id, seq, content,
                 row_number() OVER (ORDER BY dist) AS rank
          FROM vec_top
        ),
        fused AS (
          SELECT id, artifact_id, seq, content, sum(rrf) AS score FROM (
            SELECT id, artifact_id, seq, content, 1.0 / (60 + rank) AS rrf FROM bm25
            UNION ALL
            SELECT id, artifact_id, seq, content, 1.0 / (60 + rank) AS rrf FROM vec
          ) u GROUP BY id, artifact_id, seq, content
        )
        SELECT f.id::text AS chunk_id, f.artifact_id::text, f.seq, f.content, f.score,
               a.ref, a.type, a.date, a.slug
        FROM fused f JOIN artifacts a ON a.id = f.artifact_id
        WHERE a.tenant_id = CAST(:tid AS uuid)
        {type_filter}
        ORDER BY f.score DESC LIMIT :limit;
        """.replace(
            "{type_filter}",
            "AND a.type = :type_strict" if type else ""
        )
        params_hyb = {
            "tsq": ts_or,
            "qvec": qvec_str,
            "limit": limit,
            "tid": str(identity.tenant_id),
        }
        if type:
            params_hyb["type_strict"] = type
        r = await s.execute(text(sql), params_hyb)
        return {"q": q, "items": [dict(row._mapping) for row in r]}


@router.get("/{ref:path}")
async def read_one(ref: str, identity: Identity = Depends(require_identity)):
    async with app_session(identity.tenant_id) as s:
        r = await s.execute(text("""
            SELECT a.id::text, a.ref, a.type, a.date, a.slug, a.lang,
                   a.frontmatter, a.body,
                   m.display_name AS author_name
            FROM artifacts a LEFT JOIN members m ON m.id = a.author_id
            WHERE a.ref = :ref LIMIT 1
        """), {"ref": ref})
        row = r.first()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        return dict(row._mapping)
