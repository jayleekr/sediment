"""lab_curator_graph — minimal LangGraph workflow.

Flow (SPEC §8.1):
  START → SessionManager → Router → {RAG | Members | Decisions | Metadata} → Composer → Guardrails → Save → END

For MVP: simplify to a single-pass: search vault → compose answer with citations.
The Router/Memory paths are stubs that future phases will fill in.
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional, TypedDict
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from lab_lib.embeddings import embed_one
from lab_lib.logging import get_logger
from lab_lib.settings import settings

log = get_logger("graph")


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
    # Gives a tie-breaker boost to research/ for queries like GQ-017.
    "evaluation": "research",
    "harness": "research",
    "benchmark": "research",
    "agents": "research",
}


# Maps project keywords (KO/EN) to a substring of artifacts.ref. When a query
# names a project explicitly, boost artifacts under that path. Solves the
# "동아일보 관련 칼럼이나 제안" → products/donga-roi class of failures where the
# project nickname isn't in the document body verbatim.
_PROJECT_HINT_MAP: dict[str, str] = {
    "donga": "donga", "동아": "donga", "동아일보": "donga",
    "academy": "ai-architect-academy", "아카데미": "ai-architect-academy",
    "curator": "ai-curator", "큐레이터": "ai-curator",
    "simulacra": "simulacra", "시뮬라크라": "simulacra",
    "roadmap": "hypeproof-roadmap", "로드맵": "hypeproof-roadmap",
    "validation": "sediment/VALIDATION", "validator": "sediment/VALIDATION",
}


def _detect_project_path(q: str) -> str:
    """Return a substring of `artifacts.ref` implied by the query, or ''."""
    ql = q.lower()
    for kw, path in _PROJECT_HINT_MAP.items():
        if kw in ql:
            return path
    return ""


def _slug_regex(q: str) -> str:
    """POSIX regex of alphanumeric query tokens (>=3 chars) for filename match.

    A token in the query that appears in `a.slug` or the last segment of
    `a.ref` (e.g. "VALIDATION_PLAN" matches "validation"; "2026Q2" matches
    "2026") is a near-certain signal of intent. We boost those hits 2x to
    overcome BM25 score plateaus on documents that don't repeat their own
    name in the body. Use a sentinel that never matches when no tokens exist
    (instead of NULL — keeps the SQL parameter type stable for asyncpg).
    """
    tokens = re.findall(r"[A-Za-z0-9]{3,}", q)
    if not tokens:
        return "___NEVER___"
    return "(" + "|".join(re.escape(t.lower()) for t in tokens) + ")"


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


class CuratorState(TypedDict, total=False):
    tenant_id: str
    member_id: str
    conv_id: str
    query: str
    intent: str           # 'library' | 'member' | 'decision' | 'meta'
    citations: list[dict]
    answer_chunks: list[str]   # streamed tokens accumulated
    error: Optional[str]


# ============================================================
# Nodes
# ============================================================

async def node_session_manager(state: CuratorState) -> dict:
    """Set up scratchpad / context. Stub for MVP."""
    log.info("node.session.start", conv=state.get("conv_id"))
    return {}


async def node_router(state: CuratorState) -> dict:
    """Decide intent. Heuristic for MVP — Phase 4 replaces with LLM classifier.

    Content-keyword priority: if the query mentions an artifact-type keyword
    ("칼럼", "글", "리서치", "column", "research", "소설", ...), route to library
    EVEN IF the query also contains a member name. Otherwise a query like
    "라이언이 4월에 쓴 mirror-loop 칼럼" gets misrouted to `member` lookup
    (which returns 0 results since the user wants the column, not member info).
    """
    q = state.get("query", "").lower()
    # Priority order:
    # 1. meta — "칼럼 몇 개?" contains both meta + library keywords; meta wins (count query).
    # 2. library — content-type keyword wins over member name. "라이언이 쓴 칼럼" must
    #    route to library (the column), not member lookup. Restored after a P2-INTENT-03
    #    auto-fix accidentally flipped 2 and 3.
    # 3. member — only when no content-type keyword is present.
    # 4. decision — explicit "결정" / "action".
    if any(k in q for k in ["count", "summary", "총", "전체", "how many",
                              "몇 개", "몇개", "수", "신규", "지난 ", "최근 30",
                              "이번 달", "이번달"]):
        intent = "meta"
    elif any(k in q for k in ["칼럼", "글", "리서치", "research", "column",
                               "소설", "novel", "메모", "회의", "노트", "note",
                               "decision", "daily", "쓴", "작성", "published"]):
        intent = "library"
    elif any(k in q for k in ["who is", "expertise", "member", "라이언", "ryan", "jy", "kiwon"]):
        intent = "member"
    elif any(k in q for k in ["결정", "action", "액션"]):
        intent = "decision"
    else:
        intent = "library"
    log.info("node.router", intent=intent)
    return {"intent": intent}


def _build_ts_or_query(q: str) -> str:
    """Tokenize a free-text query into an OR-joined to_tsquery expression.

    plainto_tsquery uses AND between terms — too strict for short knowledge
    queries where each individual term hits but the conjunction doesn't (e.g.
    "summarize hypeproof lab activity" matches 0 chunks because no single chunk
    contains all four). We fall back to OR semantics in offline mode so BM25
    still returns relevant chunks for SSE-05 / citation flow.

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


async def node_library_search(state: CuratorState) -> dict:
    """Hybrid RAG search. Direct DB call (skipping platform HTTP for speed).

    When the embedding API is unavailable (offline / no OPENAI_API_KEY),
    embed_one returns a zero-vector. pgvector's `<=>` against a zero-vector
    yields NaN, which makes the vec branch return 0 rows. We detect this and
    use a BM25-only fallback with OR-joined terms instead.
    """
    from sqlalchemy import text
    from lab_lib.db import app_session

    q = state["query"]
    qvec = embed_one(q)
    qvec_is_zero = not any(abs(x) > 1e-9 for x in qvec)

    async with app_session(state["tenant_id"]) as s:
        if qvec_is_zero:
            # BM25 only, OR-joined for permissive matching.
            ts_or = _build_ts_or_query(q)
            if not ts_or:
                log.info("node.library.search", n=0, mode="bm25_only_empty_query")
                return {"citations": []}
            # Pass "" when no type hint — avoids asyncpg AmbiguousParameterError
            # on NULL parameters (asyncpg can't infer type from CASE expression).
            type_hint = _detect_query_type(q) or ""
            project_hint = _detect_project_path(q)
            slug_re = _slug_regex(q)
            # Type-boost (3x): artifact.type matches the implied type.
            # Project-boost (2x): ref contains the implied project path (e.g. "donga").
            # Filename-boost (2x): a token in the query appears in slug or ref —
            #   catches "VALIDATION_PLAN", "hypeproof-roadmap-2026Q2", etc.
            # Meta-doc penalty (0.5x): SPEC/README/TEST_/DECISIONS verbatim-quote
            #   validation queries, so they over-rank on every search.
            # CAST(:hint AS text) forces param type inference for asyncpg.
            sql_bm25 = """
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
                       -- Meta-doc penalty: SPEC/README/TEST_/DECISIONS verbatim-
                       -- quote validation queries, so they used to over-rank on
                       -- every search. With the project_hint + filename + type
                       -- boosts above, legitimate hits already outrank them.
                       -- Penalty kept at 0.8x (soft tiebreaker) — anything lower
                       -- broke queries that LEGITIMATELY want SPEC.md
                       -- (e.g. "AI Curator 도메인 모델"). 0.5x was found to drop
                       -- GQ-031 and GQ-033 from PASS to MISS.
                       * CASE WHEN a.ref LIKE 'products/sediment/SPEC%'
                                OR a.ref LIKE 'products/sediment/README%'
                                OR a.ref LIKE 'products/sediment/TEST_%'
                                OR a.ref LIKE 'products/sediment/DECISIONS%'
                              THEN 0.8 ELSE 1.0 END AS score,
                   a.ref, a.type, a.date::text AS date, a.slug
            FROM chunks c JOIN artifacts a ON a.id = c.artifact_id
            WHERE c.tsv @@ to_tsquery('simple', :tsq)
            ORDER BY score DESC LIMIT 6;
            """
            r = await s.execute(text(sql_bm25), {
                "tsq": ts_or,
                "type_hint": type_hint,
                "project_hint": project_hint,
                "slug_re": slug_re,
            })
            citations = [dict(row._mapping) for row in r]
            log.info("node.library.search", n=len(citations), mode="bm25_only_or")
            return {"citations": citations}

        # Hybrid path (vector + BM25 + RRF rerank)
        qvec_str = "[" + ",".join(f"{x:.6f}" for x in qvec) + "]"
        sql = """
        WITH bm25 AS (
          SELECT c.id, c.artifact_id, c.seq, c.content,
                 row_number() OVER (ORDER BY ts_rank(c.tsv, plainto_tsquery('simple', :q)) DESC) AS rank
          FROM chunks c WHERE c.tsv @@ plainto_tsquery('simple', :q) LIMIT 30
        ),
        vec AS (
          SELECT c.id, c.artifact_id, c.seq, c.content,
                 row_number() OVER (ORDER BY c.embedding <=> CAST(:qvec AS vector)) AS rank
          FROM chunks c ORDER BY c.embedding <=> CAST(:qvec AS vector) LIMIT 30
        ),
        fused AS (
          SELECT id, artifact_id, seq, content, sum(rrf) AS score FROM (
            SELECT id, artifact_id, seq, content, 1.0/(60+rank) AS rrf FROM bm25
            UNION ALL
            SELECT id, artifact_id, seq, content, 1.0/(60+rank) AS rrf FROM vec
          ) u GROUP BY id, artifact_id, seq, content
        )
        SELECT f.id::text AS chunk_id, f.artifact_id::text, f.seq, f.content,
               f.score::float AS score, a.ref, a.type, a.date::text AS date, a.slug
        FROM fused f JOIN artifacts a ON a.id = f.artifact_id
        ORDER BY f.score DESC LIMIT 6;
        """
        r = await s.execute(text(sql), {"q": q, "qvec": qvec_str})
        citations = [dict(row._mapping) for row in r]
    log.info("node.library.search", n=len(citations), mode="hybrid")
    return {"citations": citations}


_MEMBER_NAME_HINTS = [
    "ryan", "jy", "kiwon", "tj", "bh", "sebastian", "jay",
    "라이언", "지웅", "신진용", "남기원", "강태진", "태봉호",
]


def _extract_member_terms(q: str) -> list[str]:
    """Pull out likely member-name tokens from a free-text query.

    "Ryan은 누구인가" -> ["ryan"]
    "JY가 작성한 글" -> ["jy"]
    "남기원의 마케팅 글" -> ["남기원"]
    Falls back to the whole query if nothing matches (legacy behavior).
    """
    ql = q.lower()
    hits = [name for name in _MEMBER_NAME_HINTS if name in ql or name in q]
    return hits or [q]


async def node_member_lookup(state: CuratorState) -> dict:
    from sqlalchemy import text
    from lab_lib.db import app_session
    q = state["query"]
    terms = _extract_member_terms(q)
    async with app_session(state["tenant_id"]) as s:
        all_results: list[dict] = []
        for term in terms:
            r = await s.execute(text("""
                SELECT display_name, real_name, role, title, expertise, interests, external_id
                FROM members
                WHERE display_name ILIKE :p OR real_name ILIKE :p OR title ILIKE :p
                   OR expertise::text ILIKE :p
                LIMIT 5
            """), {"p": f"%{term}%"})
            for row in r:
                d = dict(row._mapping)
                if d not in all_results:
                    all_results.append(d)
            if all_results:
                break
    return {"citations": [{"type": "member", **m} for m in all_results[:5]]}


async def node_meta_summary(state: CuratorState) -> dict:
    from sqlalchemy import text
    from lab_lib.db import app_session
    async with app_session(state["tenant_id"]) as s:
        r = await s.execute(text(
            "SELECT type, count(*) AS n FROM artifacts GROUP BY type ORDER BY n DESC"))
        rows = [dict(row._mapping) for row in r]
    return {"citations": [{"type": "summary", "by_type": rows}]}


async def node_compose(state: CuratorState) -> dict:
    """Compose final answer. For MVP: structured citation cards + LLM synthesis."""
    return {}  # streaming happens in the SSE handler, not here


async def node_guardrails(state: CuratorState) -> dict:
    """Off-topic / PII guard. Stub for MVP."""
    return {}


async def node_save(state: CuratorState) -> dict:
    return {}


# ============================================================
# Graph builder
# ============================================================

def build_graph():
    g = StateGraph(CuratorState)
    g.add_node("session", node_session_manager)
    g.add_node("router", node_router)
    g.add_node("library", node_library_search)
    g.add_node("member", node_member_lookup)
    g.add_node("meta", node_meta_summary)
    g.add_node("compose", node_compose)
    g.add_node("guardrails", node_guardrails)
    g.add_node("save", node_save)

    g.set_entry_point("session")
    g.add_edge("session", "router")

    def route_fn(state: CuratorState) -> str:
        return state.get("intent", "library")

    g.add_conditional_edges("router", route_fn, {
        "library": "library",
        "member": "member",
        "decision": "library",   # Phase 4: separate node
        "meta": "meta",
    })
    g.add_edge("library", "compose")
    g.add_edge("member", "compose")
    g.add_edge("meta", "compose")
    g.add_edge("compose", "guardrails")
    g.add_edge("guardrails", "save")
    g.add_edge("save", END)

    return g.compile(checkpointer=MemorySaver())
