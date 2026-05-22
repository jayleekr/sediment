"""Library — vault browser + search."""
from __future__ import annotations
import re
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from lab_lib.auth import Identity, require_identity
from lab_lib.db import app_session
from lab_lib.embeddings import embed_one

# English stop-words filtered out of BM25 OR-tsquery. PostgreSQL's
# to_tsquery('simple', ...) does NOT remove stop-words, so without this filter
# common tokens like "is", "the", "what", "about" generate spurious matches
# against any document containing them — drowning out real signal in long
# corpora and crashing recall@3. See P1-GOLDEN-RAG-01.
# Korean tokens (가-힣 range) are NEVER filtered — they're rare and meaningful.
_STOP_WORDS = frozenset({
    "is", "the", "a", "an", "of", "in", "at", "to", "for", "on", "with", "by",
    "about", "what", "how", "why", "when", "where", "which", "this", "that",
    "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "will", "would", "could", "should", "may", "might",
    "its", "it", "and", "or", "if", "not", "but", "so", "too", "also",
    "can", "more", "my", "me", "we", "you", "he", "she", "they", "their",
})

# Korean particle suffixes stripped when normalizing BM25 tokens.
# "라이언이" → "라이언", "4월에" → "4월". Ordered longest-first to avoid partial strips.
# Only strips when the remaining base form is >= 2 chars. See P1-RAG-KO-particle.
_KO_PARTICLE_SUFFIXES: tuple[str, ...] = (
    "입니다", "했던",
    "하는", "에서", "에게", "으로", "이다",
    "한", "쓴",
    "이", "가", "을", "를", "의", "에", "로", "는", "은", "와", "과",
)


def _strip_korean_particles(tok: str) -> str | None:
    """Return tok with one trailing Korean particle removed, or None if no match."""
    for p in _KO_PARTICLE_SUFFIXES:
        if tok.endswith(p):
            base = tok[: -len(p)]
            if len(base) >= 2:
                return base
    return None


# Maps query keywords to artifact type for BM25 type-boosting (3x weight).
# When a query contains a type hint, boost matching artifact types so that
# e.g. "AI 보안 칼럼" returns column artifacts ahead of SPEC.md/README.md.
_TYPE_HINT_MAP: dict[str, str] = {
    "칼럼": "column",
    "column": "column",
    "리서치": "research",
    "research": "research",
    "daily": "research",
    "소설": "novel",
    "novel": "novel",
    # Research-typical signals — "evaluation harness", "agents", "benchmark"
    # phrasing comes from daily research notes far more often than columns.
    "evaluation": "research",
    "harness": "research",
    "benchmark": "research",
    "agents": "research",
}


def _detect_query_type(q: str) -> Optional[str]:
    """Return artifact-type hint implied by the query, or None.

    Uses token-based matching (not substring) to avoid false positives like
    "칼럼이나" (= column-or) triggering the column boost for non-column queries.
    """
    tokens = set(re.findall(r"[A-Za-z0-9가-힣]+", q.lower()))
    for keyword, atype in _TYPE_HINT_MAP.items():
        if keyword in tokens:
            return atype
    return None


# Maps project keywords (KO/EN) to a substring of artifacts.ref. When a query
# names a project explicitly, boost artifacts under that path. Solves the
# "동아일보 관련 칼럼이나 제안" → products/donga-roi class of failures where the
# project nickname isn't in the document body verbatim. Mirrored from
# lab_curator_graph.
_PROJECT_HINT_MAP: dict[str, str] = {
    "donga": "donga", "동아": "donga", "동아일보": "donga",
    "academy": "ai-architect-academy", "아카데미": "ai-architect-academy",
    "curator": "ai-curator", "큐레이터": "ai-curator",
    "simulacra": "simulacra", "시뮬라크라": "simulacra",
    "roadmap": "hypeproof-roadmap", "로드맵": "hypeproof-roadmap",
    "validation": "sediment/VALIDATION", "validator": "sediment/VALIDATION",
}


def _detect_project_path(q: str) -> str:
    ql = q.lower()
    for kw, path in _PROJECT_HINT_MAP.items():
        if kw in ql:
            return path
    return ""


def _slug_regex(q: str) -> str:
    """POSIX regex of alphanumeric query tokens (>=3 chars) for filename match.

    A token that appears in `a.slug` or the last segment of `a.ref` is a
    near-certain signal of intent. We boost those hits 2x to overcome BM25
    score plateaus on documents that don't repeat their own name in the body.
    Use a sentinel that never matches when no tokens exist (keeps the SQL
    parameter type stable for asyncpg).
    """
    tokens = re.findall(r"[A-Za-z0-9]{3,}", q)
    if not tokens:
        return "___NEVER___"
    return "(" + "|".join(re.escape(t.lower()) for t in tokens) + ")"

router = APIRouter()


def _build_ts_or_query(q: str) -> str:
    """Tokenize free-text query into an OR-joined to_tsquery expression.

    plainto_tsquery uses AND between terms — too strict for offline mode where
    embedding API is unavailable and we can't fall back to vector similarity.
    Mirrors the pattern used by lab_curator_graph.node_library_search so that
    the platform /search endpoint behaves identically to the LangGraph node.

    English stop-words are filtered out (see _STOP_WORDS); Korean tokens are
    always kept because to_tsquery('simple', ...) won't dedupe them and they
    carry strong signal in this corpus.
    """
    raw = re.findall(r"[A-Za-z0-9가-힣_]+", q)
    result: list[str] = []
    seen: set[str] = set()

    def _add(tok: str) -> None:
        if tok not in seen:
            seen.add(tok)
            result.append(tok)

    for t in raw:
        t_lower = t.lower()
        if len(t_lower) < 2:
            continue
        has_korean = any("가" <= c <= "힣" for c in t_lower)
        if has_korean:
            _add(t_lower)
            # Strip Korean particles — "라이언이" → "라이언", "4월에" → "4월" —
            # so tokens match the base forms stored in the tsvector index.
            stripped = _strip_korean_particles(t_lower)
            if stripped is not None:
                _add(stripped)
            # Mixed Latin+Korean (e.g. "learning이"): also emit Latin-only form
            # so it matches ts_vectors that stored the term without Korean postfix.
            latin_only = re.sub(r"[가-힣]+", "", t_lower)
            if latin_only and len(latin_only) >= 2 and latin_only not in _STOP_WORDS:
                _add(latin_only)
        elif t_lower not in _STOP_WORDS:
            _add(t_lower)

    if not result:
        return ""
    return " | ".join(result)


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
        sql += " AND a.type = :type"; params["type"] = type
    if author_external_id:
        sql += " AND m.external_id = :eid"; params["eid"] = author_external_id
    if lens:
        sql += " AND a.frontmatter -> 'lens' ? :lens"; params["lens"] = lens
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
async def search(q: str, limit: int = 8, identity: Identity = Depends(require_identity)):
    """Hybrid BM25 + vector search. Convenience wrapper for the UI.

    Offline-mode fallback: when the embedding API is unavailable (no
    OPENAI_API_KEY), embed_one returns a zero-vector. pgvector's `<=>` against
    a zero-vector yields NaN, so the vec branch contributes nothing AND the
    BM25 branch alone uses AND-joined plainto_tsquery (too strict for short
    knowledge queries). Detect zero-vector and switch to BM25-only with
    OR-joined to_tsquery — mirrors lab_curator_graph.node_library_search.
    """
    qvec = embed_one(q)
    qvec_is_zero = not any(abs(x) > 1e-9 for x in qvec)

    async with app_session(identity.tenant_id) as s:
        if qvec_is_zero:
            # Offline path: BM25 only, OR-joined for permissive matching.
            ts_or = _build_ts_or_query(q)
            if not ts_or:
                return {"q": q, "items": []}
            # Pass "" when no type hint — avoids asyncpg AmbiguousParameterError
            # on NULL parameters (asyncpg can't infer type from CASE expression).
            type_hint = _detect_query_type(q) or ""
            project_hint = _detect_project_path(q)
            slug_re = _slug_regex(q)
            # Boost stack (multiplicative on ts_rank):
            #   type-boost 3x      — artifact.type matches implied type
            #   project-boost 2x   — ref contains project keyword (donga, academy, …)
            #   filename-boost 2x  — query token appears in ref or slug
            #   meta-doc penalty   — SPEC/README/TEST_/DECISIONS halved (they
            #                        verbatim-quote validation queries)
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
                           -- Soft 0.8x meta-doc penalty (was 0.5x → broke
                           -- GQ-031/GQ-033 which legitimately wanted SPEC.md).
                           -- The new boosts above carry most of the work now.
                           * CASE WHEN a.ref LIKE 'products/sediment/SPEC%'
                                    OR a.ref LIKE 'products/sediment/README%'
                                    OR a.ref LIKE 'products/sediment/TEST_%'
                                    OR a.ref LIKE 'products/sediment/DECISIONS%'
                                  THEN 0.8 ELSE 1.0 END AS score,
                       a.ref, a.type, a.date, a.slug
                FROM chunks c JOIN artifacts a ON a.id = c.artifact_id
                WHERE c.tsv @@ to_tsquery('simple', :tsq)
            ),
            deduped AS (
                SELECT DISTINCT ON (artifact_id)
                       chunk_id, artifact_id, seq, content, score, ref, type, date, slug
                FROM raw
                ORDER BY artifact_id, score DESC
            )
            SELECT chunk_id, artifact_id, seq, content, score, ref, type, date, slug
            FROM deduped
            ORDER BY score DESC LIMIT :limit;
            """
            r = await s.execute(text(sql_bm25), {
                "tsq": ts_or, "limit": limit, "type_hint": type_hint,
                "project_hint": project_hint, "slug_re": slug_re,
            })
            return {"q": q, "items": [dict(row._mapping) for row in r]}

        # Online path: hybrid BM25 + vector with RRF rerank (unchanged).
        qvec_str = "[" + ",".join(f"{x:.6f}" for x in qvec) + "]"
        sql = """
        WITH bm25 AS (
          SELECT c.id, c.artifact_id, c.seq, c.content,
                 row_number() OVER (ORDER BY ts_rank(c.tsv, plainto_tsquery('simple', :q)) DESC) AS rank
          FROM chunks c WHERE c.tsv @@ plainto_tsquery('simple', :q) LIMIT 50
        ),
        vec AS (
          SELECT c.id, c.artifact_id, c.seq, c.content,
                 row_number() OVER (ORDER BY c.embedding <=> CAST(:qvec AS vector)) AS rank
          FROM chunks c ORDER BY c.embedding <=> CAST(:qvec AS vector) LIMIT 50
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
        ORDER BY f.score DESC LIMIT :limit;
        """
        r = await s.execute(text(sql), {"q": q, "qvec": qvec_str, "limit": limit})
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
