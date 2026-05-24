"""lab_curator_graph — minimal LangGraph workflow.

Flow (SPEC §8.1):
  START → SessionManager → Router → {RAG | Members | Decisions | Metadata} → Composer → Guardrails → Save → END

For MVP: simplify to a single-pass: search vault → compose answer with citations.
The Router/Memory paths are stubs that future phases will fill in.
"""
from __future__ import annotations
import json
import re
from typing import Optional, TypedDict
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from lab_lib.embeddings import embed_one
from lab_lib.logging import get_logger

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


def _normalize_citation_row(row: dict) -> dict:
    """Normalize DB JSON/JSONB fields before SSE/persistence serialization."""
    for key in ("provenance", "decision_provenance"):
        value = row.get(key)
        if isinstance(value, str):
            try:
                row[key] = json.loads(value)
            except json.JSONDecodeError:
                row[key] = {"raw": value, "parse_error": True}
    return row


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
    # Priority order:
    # 0. freshness — "최신/latest" + a noun → deterministic SQL ORDER BY date,
    #    bypassing RAG (which can't reliably judge dates from RRF-ranked
    #    citations). Added per sediment#16 #4.
    # 1. meta — "칼럼 몇 개?" contains both meta + library keywords; meta wins (count query).
    # 2. library — content-type keyword wins over member name. "라이언이 쓴 칼럼" must
    #    route to library (the column), not member lookup.
    # 3. member — only when no content-type keyword is present.
    # 4. decision — explicit "결정" / "action".
    # Priority 0a — `elaborate`: user is asking to expand on the IMMEDIATE
    # prior turn ("디테일하게/더 자세히/explain more/expand on"). Must NOT
    # re-search — reuses the prior assistant turn's citations and asks the
    # LLM to expand each one. Added per UX critique: re-searching a short
    # follow-up like "디테일하게 설명해" returns BM25-relevant-but-wrong
    # content.
    if _is_elaborate_query(q):
        # Only route to elaborate if there IS a prior assistant turn to
        # expand. Otherwise fall through to library (treat as a fresh ask).
        if state.get("conv_id"):
            intent = "elaborate"
        else:
            intent = "library"
    elif _is_freshness_query(q):
        intent = "freshness"
    elif any(k in q for k in ["count", "summary", "총", "전체", "how many",
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


# Elaborate-intent detector — user wants MORE DEPTH on what was just
# discussed, not a new search. These follow-up patterns should reuse
# prior citations + tell the LLM to expand each.
_ELABORATE_KEYWORDS = [
    "디테일하게", "더 자세히", "자세히 설명", "자세하게",
    "구체적으로", "상세히", "상세하게",
    "explain more", "expand on", "in more detail", "elaborate",
    "go deeper", "tell me more",
    "더 알려줘", "더 설명", "조금 더",
]


def _is_elaborate_query(ql: str) -> bool:
    """Heuristic — true when the query is asking to expand the prior turn."""
    return any(kw in ql for kw in _ELABORATE_KEYWORDS)


# Freshness intent detector — keyword-based. Tight enough to avoid false
# positives (we don't want every query to bypass RAG); broad enough to catch
# the obvious cases. See sediment#16 #4 for the motivating bug.
_FRESHNESS_KEYWORDS = [
    "최신", "가장 최근", "가장 최신", "최근에", "최근의",
    "latest", "newest", "most recent", "last week", "this week",
    "어제", "오늘", "yesterday", "today",
    "언제꺼", "언제 거", "언제거",
]


def _is_freshness_query(ql: str) -> bool:
    """Heuristic — true when the query is asking 'what's the newest X'."""
    return any(kw in ql for kw in _FRESHNESS_KEYWORDS)


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

    # Defense-in-depth: every retrieval query carries an explicit tenant_id
    # filter (in addition to RLS, which SHOULD also apply via app_session).
    # If the DB role is misconfigured (e.g., BYPASSRLS superuser instead of
    # the intended rls-subject role), this clause still prevents cross-tenant
    # leak. See sediment#16 for the prod incident that drove this.
    tid = str(state["tenant_id"])

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
                   a.ref, a.type, a.date::text AS date, a.slug,
                   a.frontmatter -> 'provenance' AS provenance,
                   CASE
                     WHEN a.type = 'decision'
                       THEN COALESCE(a.frontmatter -> 'provenance', '{}'::jsonb)
                            || jsonb_build_object('missing', NOT (a.frontmatter ? 'provenance'))
                     ELSE NULL
                   END AS decision_provenance
            FROM chunks c JOIN artifacts a ON a.id = c.artifact_id
            WHERE c.tsv @@ to_tsquery('simple', :tsq)
              -- Defense-in-depth tenant filter (see sediment#16)
              AND a.tenant_id = CAST(:tid AS uuid)
              AND c.tenant_id = CAST(:tid AS uuid)
            ORDER BY score DESC LIMIT 8;
            """
            r = await s.execute(text(sql_bm25), {
                "tsq": ts_or,
                "type_hint": type_hint,
                "project_hint": project_hint,
                "slug_re": slug_re,
                "tid": tid,
            })
            citations = [_normalize_citation_row(dict(row._mapping)) for row in r]
            log.info("node.library.search", n=len(citations), mode="bm25_only_or")
            return {"citations": citations}

        # Hybrid path (vector + BM25 + RRF rerank)
        # Defense-in-depth: tenant filter on BOTH source CTEs (bm25 + vec)
        # so the fused set can never contain cross-tenant rows. See sediment#16.
        #
        # BM25 uses to_tsquery + OR-joined tokens (same as offline path) instead
        # of plainto_tsquery (AND-joined). Why: AND between tokens silently
        # drops every multi-token query where no single chunk contains all
        # tokens — e.g. "BH가 누구야?" / "태봉호는 뭐하는자식이야" returned 0 BM25
        # hits even though the entity name is in the corpus (sediment#52). vec
        # alone can't recover on short Korean entity queries, so the BM25
        # branch must do its share. ts_or="" (no tokens) → bm25 CTE returns 0
        # rows and the fused result falls back to vec-only naturally.
        # P0 perf fix (sediment#58): two-stage CTEs so LIMIT push-down works.
        #
        # Previous structure put `row_number() OVER (ORDER BY ts_rank(...))` and
        # `row_number() OVER (ORDER BY embedding <=> qvec)` INSIDE the bm25/vec
        # CTE, BEFORE the outer LIMIT 50. Postgres semantics force the window
        # function to run over EVERY row that matched WHERE — full ts_rank
        # compute + full sort, then chop to 50. With OR-joined Korean BM25 the
        # matching set explodes; with no ANN index hit on the vec path the
        # tenant-wide vector scan blows up identically.
        #
        # Splitting into *_top (predicate + ORDER BY + LIMIT 50) and then *_ranked
        # (row_number assigned to the already-truncated 50 rows) lets the planner
        # push LIMIT to GIN/HNSW top-K. ts_rank runs on the LIMIT-truncated set,
        # not the full match list. Identical RRF math, no semantic change.
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
            SELECT id, artifact_id, seq, content, 1.0/(60+rank) AS rrf FROM bm25
            UNION ALL
            SELECT id, artifact_id, seq, content, 1.0/(60+rank) AS rrf FROM vec
          ) u GROUP BY id, artifact_id, seq, content
        ),
        -- Per-artifact dedup: keep the highest-scoring chunk per artifact so a
        -- single long doc can't dominate top-N. Matches the offline BM25 path
        -- and the platform /search endpoint behavior.
        deduped AS (
          SELECT DISTINCT ON (artifact_id)
                 id, artifact_id, seq, content, score
          FROM fused
          ORDER BY artifact_id, score DESC
        )
        SELECT f.id::text AS chunk_id, f.artifact_id::text, f.seq, f.content,
               f.score::float AS score, a.ref, a.type, a.date::text AS date, a.slug,
               a.frontmatter -> 'provenance' AS provenance,
               CASE
                 WHEN a.type = 'decision'
                   THEN COALESCE(a.frontmatter -> 'provenance', '{}'::jsonb)
                        || jsonb_build_object('missing', NOT (a.frontmatter ? 'provenance'))
                 ELSE NULL
               END AS decision_provenance
        FROM deduped f JOIN artifacts a ON a.id = f.artifact_id
        WHERE a.tenant_id = CAST(:tid AS uuid)
        ORDER BY f.score DESC LIMIT 8;
        """
        r = await s.execute(text(sql), {"tsq": ts_or, "qvec": qvec_str, "tid": tid})
        citations = [_normalize_citation_row(dict(row._mapping)) for row in r]
    log.info("node.library.search", n=len(citations), mode="hybrid", ts_or_empty=(not ts_or))
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
    """Member lookup — defense-in-depth tenant filter (see sediment#16)."""
    from sqlalchemy import text
    from lab_lib.db import app_session
    q = state["query"]
    terms = _extract_member_terms(q)
    tid = str(state["tenant_id"])
    async with app_session(state["tenant_id"]) as s:
        all_results: list[dict] = []
        for term in terms:
            r = await s.execute(text("""
                SELECT display_name, real_name, role, title, expertise, interests, external_id
                FROM members
                WHERE tenant_id = CAST(:tid AS uuid)
                  AND (display_name ILIKE :p OR real_name ILIKE :p OR title ILIKE :p
                       OR expertise::text ILIKE :p)
                LIMIT 5
            """), {"p": f"%{term}%", "tid": tid})
            for row in r:
                d = dict(row._mapping)
                if d not in all_results:
                    all_results.append(d)
            if all_results:
                break
    return {"citations": [{"type": "member", **m} for m in all_results[:5]]}


_TYPE_FROM_QUERY = [
    ("research", ["리서치", "research", "daily-research", "daily research"]),
    ("decision", ["결정", "decision", "adr"]),
    ("meeting", ["회의", "meeting"]),
    ("column", ["칼럼", "column"]),
    ("novel", ["소설", "novel"]),
    ("note", ["메모", "note"]),
]


def _detect_freshness_type(q: str) -> str | None:
    """Pull an artifact `type` hint from a freshness query, if obvious.
    'latest research' → research. Otherwise None (return all types)."""
    ql = q.lower()
    for t, kws in _TYPE_FROM_QUERY:
        if any(kw in ql for kw in kws):
            return t
    return None


async def node_freshness_lookup(state: CuratorState) -> dict:
    """Deterministic 'what's the most recent X' query — no RAG, no LLM.

    Added per sediment#16 #4 — the previous behavior routed 'latest' queries
    to library (RAG) where the LLM had to pick 'latest' from RRF-ranked
    citations. It hallucinated. This node uses SQL ORDER BY to return the
    actual N newest artifacts (optionally filtered by inferred type), and
    the compose step renders them as a list without LLM judgment.

    Tenant filter explicit (sediment#16 defense-in-depth).
    """
    from sqlalchemy import text
    from lab_lib.db import app_session
    q = state["query"]
    tid = str(state["tenant_id"])
    type_hint = _detect_freshness_type(q)

    async with app_session(state["tenant_id"]) as s:
        # COALESCE(date, updated_at::date) picks frontmatter `date` when
        # available (canonical for daily-research / meeting notes), falls
        # back to ingest timestamp for artifacts without an explicit date.
        params = {"tid": tid}
        type_filter = ""
        if type_hint:
            type_filter = "AND type = :t"
            params["t"] = type_hint
        sql = f"""
            SELECT ref, type, date::text AS date, updated_at::text AS ingested_at
            FROM artifacts
            WHERE tenant_id = CAST(:tid AS uuid)
              {type_filter}
            ORDER BY COALESCE(date, updated_at::date) DESC, updated_at DESC
            LIMIT 5
        """
        r = await s.execute(text(sql), params)
        rows = [dict(row._mapping) for row in r]

    log.info("node.freshness", n=len(rows), type_hint=type_hint or "any")
    # Render as `freshness` citations — compose step prints them verbatim
    # (no LLM "judgment" of which is newest).
    return {
        "citations": [
            {"type": "freshness", "rank": i + 1, **row}
            for i, row in enumerate(rows)
        ],
        "intent_hint": type_hint or "any",
    }


async def node_elaborate(state: CuratorState) -> dict:
    """Reuse the immediate prior assistant turn's citations + tell the LLM
    to expand each one. NO new retrieval. NO new search.

    Why: short follow-ups like "디테일하게 설명해" are anaphoric — they
    want depth on what was JUST discussed, not a new topic. RAG can't
    distinguish "expand" from "search" — when retrieval re-fires on a
    short query, RRF pulls semantically-related-but-tangential content
    and the LLM dutifully answers about THAT (the 필라멘트리 incident).

    Mechanic: load the MOST RECENT assistant message for this conv_id,
    pull its citations array as-is, return them so compose can render
    against the original sources. Compose's system prompt also reads
    `state.intent == "elaborate"` and switches to expand-mode (drop the
    "≤ 4 short paragraphs" cap).
    """
    from sqlalchemy import text
    from lab_lib.db import app_session
    tid = str(state["tenant_id"])
    conv_id = state["conv_id"]

    async with app_session(state["tenant_id"]) as s:
        r = await s.execute(text("""
            SELECT citations, content
            FROM messages
            WHERE tenant_id = CAST(:tid AS uuid)
              AND conv_id = CAST(:cid AS uuid)
              AND role = 'assistant'
              AND citations IS NOT NULL
              AND jsonb_array_length(citations) > 0
            ORDER BY created_at DESC
            LIMIT 1
        """), {"tid": tid, "cid": conv_id})
        row = r.first()

    if not row:
        # No prior cited turn → fall back to library search (caller
        # routes accordingly via the empty `citations` signal)
        log.info("node.elaborate.no_prior", conv=conv_id)
        return {"citations": [], "elaborate_fell_back": True}

    prior_citations = list(row[0] or [])
    prior_answer = row[1] or ""
    log.info("node.elaborate.reuse",
             conv=conv_id, citations=len(prior_citations))
    # Re-emit the prior citations so compose has them. Add a sentinel field
    # so compose's prompt can detect this and switch to expand-mode.
    return {
        "citations": prior_citations,
        "elaborate_prior_answer": prior_answer[:1500],   # cap for context
    }


async def node_meta_summary(state: CuratorState) -> dict:
    """Per-tenant artifact counts. Explicit tenant filter (see sediment#16)."""
    from sqlalchemy import text
    from lab_lib.db import app_session
    tid = str(state["tenant_id"])
    async with app_session(state["tenant_id"]) as s:
        r = await s.execute(text("""
            SELECT type, count(*) AS n FROM artifacts
            WHERE tenant_id = CAST(:tid AS uuid)
            GROUP BY type ORDER BY n DESC
        """), {"tid": tid})
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
    g.add_node("freshness", node_freshness_lookup)    # sediment#16 #4
    g.add_node("elaborate", node_elaborate)           # UX critique 2026-05-22
    g.add_node("compose", node_compose)
    g.add_node("guardrails", node_guardrails)
    g.add_node("save", node_save)

    g.set_entry_point("session")
    g.add_edge("session", "router")

    def route_fn(state: CuratorState) -> str:
        return state.get("intent", "library")

    g.add_conditional_edges("router", route_fn, {
        "library":   "library",
        "member":    "member",
        "decision":  "library",   # Phase 4: separate node
        "meta":      "meta",
        "freshness": "freshness",
        "elaborate": "elaborate",
    })
    g.add_edge("library", "compose")
    g.add_edge("member", "compose")
    g.add_edge("meta", "compose")
    g.add_edge("freshness", "compose")
    g.add_edge("elaborate", "compose")
    g.add_edge("compose", "guardrails")
    g.add_edge("guardrails", "save")
    g.add_edge("save", END)

    return g.compile(checkpointer=MemorySaver())
