"""sediment_graph — minimal LangGraph workflow.

Flow (SPEC §8.1):
  START → SessionManager → Router → {RAG | Members | Decisions | Metadata} → Composer → Guardrails → Save → END

For MVP: simplify to a single-pass: search vault → compose answer with citations.
The Router/Memory paths are stubs that future phases will fill in.
"""
from __future__ import annotations
import json
from typing import Optional, TypedDict
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from lab_lib.embeddings import embed_one
from lab_lib.logging import get_logger
# WO-7 2026-05-23: search helpers extracted to lab_lib.search_utils.
# The 130 lines previously here were verbatim-duplicated with library.py
# (and the workspace_mcp.py zero-vector guard was MISSING — a confirmed
# LEARNINGS-class drift). Local underscore aliases preserved so any
# remaining call sites continue to resolve without further edits.
from lab_lib.search_utils import (
    detect_query_type as _detect_query_type,
    detect_project_path as _detect_project_path,
    slug_regex as _slug_regex,
    build_ts_or_query as _build_ts_or_query,
    prefer_bm25_first,
    is_zero_vector,
)

log = get_logger("graph")


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


class SedimentState(TypedDict, total=False):
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

async def node_session_manager(state: SedimentState) -> dict:
    """Set up scratchpad / context. Stub for MVP."""
    log.info("node.session.start", conv=state.get("conv_id"))
    return {}


async def node_router(state: SedimentState) -> dict:
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


# _build_ts_or_query was a second copy of the function imported above as
# `_build_ts_or_query` from lab_lib.search_utils. Deleted 2026-05-23 (WO-7).


async def node_library_search(state: SedimentState) -> dict:
    """Hybrid RAG search. Direct DB call (skipping platform HTTP for speed).

    When the embedding API is unavailable (offline / no OPENAI_API_KEY),
    embed_one returns a zero-vector. pgvector's `<=>` against a zero-vector
    yields NaN, which makes the vec branch return 0 rows. We detect this and
    use a BM25-only fallback with OR-joined terms instead.
    """
    from sqlalchemy import text
    from lab_lib.db import app_session

    q = state["query"]
    bm25_first = prefer_bm25_first(q)
    qvec = [0.0] if bm25_first else embed_one(q)
    qvec_is_zero = bm25_first or is_zero_vector(qvec)

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
                       -- (e.g. "Sediment 도메인 모델"). 0.5x was found to drop
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
            log.info(
                "node.library.search",
                n=len(citations),
                mode="bm25_only_or",
                bm25_first=bm25_first,
            )
            return {"citations": citations}

        # Hybrid path (vector + BM25 + RRF rerank)
        # Defense-in-depth: tenant filter on BOTH source CTEs (bm25 + vec)
        # so the fused set can never contain cross-tenant rows. See sediment#16.
        #
        # BM25 uses to_tsquery + OR-joined tokens (same as offline path) instead
        # of plainto_tsquery (AND-joined). Why: AND between tokens silently
        # drops every multi-token query where no single chunk contains all
        # tokens — e.g. a Korean entity-name query like "X는 누구야?" returned 0
        # BM25 hits even though the entity name was in the corpus (sediment#52).
        # vec alone can't recover on short Korean entity queries, so the BM25
        # branch must do its share. ts_or="" (no tokens) → bm25 CTE returns 0
        # rows and the fused result falls back to vec-only naturally.
        # P0 perf fix (sediment#58): two-stage CTEs so LIMIT push-down works.
        #
        # Previous structure assigned window ranks inside the bm25/vec CTEs,
        # before the outer LIMIT 50. Postgres semantics force that window work
        # over EVERY row that matched WHERE: full ts_rank compute + full sort,
        # then chop to 50. With OR-joined Korean BM25 the matching set explodes;
        # with no ANN index hit on the vec path the tenant-wide vector scan
        # blows up identically.
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


# Routing hints for the member-intent classifier.
#
# Public OSS default is intentionally narrow — `jay` (project owner) plus two
# token-style fixtures (`ryan`, `라이언`) that keep the routing-logic tests
# meaningful without baking a real-world team roster into the source tree.
# Private deployments should extend this with their own team's display names
# (mirrors what's in data/members.json).
_MEMBER_NAME_HINTS = [
    "jay", "ryan", "라이언",
]


def _extract_member_terms(q: str) -> list[str]:
    """Pull out likely member-name tokens from a free-text query.

    Examples (with the default OSS hints):
      "Ryan은 누구인가" -> ["ryan"]
      "라이언이 쓴 칼럼"  -> ["라이언"]
    Falls back to the whole query if nothing matches (legacy behavior).
    """
    ql = q.lower()
    hits = [name for name in _MEMBER_NAME_HINTS if name in ql or name in q]
    return hits or [q]


async def node_member_lookup(state: SedimentState) -> dict:
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


async def node_freshness_lookup(state: SedimentState) -> dict:
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


async def node_elaborate(state: SedimentState) -> dict:
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


async def node_meta_summary(state: SedimentState) -> dict:
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


async def node_compose(state: SedimentState) -> dict:
    """Compose final answer. For MVP: structured citation cards + LLM synthesis."""
    return {}  # streaming happens in the SSE handler, not here


async def node_guardrails(state: SedimentState) -> dict:
    """Off-topic / PII guard. Stub for MVP."""
    return {}


async def node_save(state: SedimentState) -> dict:
    return {}


# ============================================================
# Graph builder
# ============================================================

def build_graph():
    g = StateGraph(SedimentState)
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

    def route_fn(state: SedimentState) -> str:
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
