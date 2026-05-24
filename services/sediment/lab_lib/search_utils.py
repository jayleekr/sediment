"""Shared search/retrieval helpers.

Extracted 2026-05-23 (WO-7) to close the duplication that LEARNINGS predicted
would drift and HAS drifted: `_STOP_WORDS` lived independently in
``applications/sediment_platform/routers/library.py`` and
``applications/sediment_langgraph/graphs/lab_curator_graph.py``, with the
``workspace_mcp.py`` MCP tool missing the zero-vector guard entirely
(REPORT.md HIGH — confirmed recurrence). One canonical module:

  - ``_STOP_WORDS``               English stop-words removed from BM25 OR-queries
  - ``_KO_PARTICLE_SUFFIXES``     ordered longest-first; strip particles from BM25
  - ``strip_korean_particles``    return base form or None
  - ``_TYPE_HINT_MAP``            keyword → artifact type for BM25 type-boost
  - ``detect_query_type``         token-level match (NOT substring — see "칼럼이나" bug)
  - ``_PROJECT_HINT_MAP``         keyword → artifact.ref substring for project boost
  - ``detect_project_path``       return matching substring or ''
  - ``slug_regex``                tokens regex for filename match boost
  - ``build_ts_or_query``         the OR-joined to_tsquery expression
  - ``is_zero_vector``            offline-mode embedding detector

When you find yourself copy-pasting any of the above into a new retrieval
path, STOP and import from here instead. The triplication LEARNINGS warned
about already cost one recurrence; the next will too.
"""
from __future__ import annotations
import re
from typing import Optional, Sequence


# ── English stop-words removed from BM25 OR-queries ───────────────────────
# Why: to_tsquery('simple', ...) doesn't strip them, so common-token noise
# matches every document — drowning real signal in long corpora and crashing
# recall@3 (P1-GOLDEN-RAG-01).
# Korean tokens (가-힣 range) are NEVER filtered — they're rare and meaningful.
_STOP_WORDS = frozenset({
    "is", "the", "a", "an", "of", "in", "at", "to", "for", "on", "with", "by",
    "about", "what", "how", "why", "when", "where", "which", "this", "that",
    "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "will", "would", "could", "should", "may", "might",
    "its", "it", "and", "or", "if", "not", "but", "so", "too", "also",
    "can", "more", "my", "me", "we", "you", "he", "she", "they", "their",
})


# ── Korean particle suffixes stripped from BM25 tokens ────────────────────
# "라이언이" → "라이언", "4월에" → "4월". Ordered longest-first to avoid
# partial strips. Only strips when remaining base form is >= 2 chars.
# See P1-RAG-KO-particle.
_KO_PARTICLE_SUFFIXES: tuple[str, ...] = (
    "입니다", "했던",
    "하는", "에서", "에게", "으로", "이다",
    "한", "쓴",
    "이", "가", "을", "를", "의", "에", "로", "는", "은", "와", "과",
)


def strip_korean_particles(tok: str) -> Optional[str]:
    """Return tok with one trailing Korean particle removed, or None if no match."""
    for p in _KO_PARTICLE_SUFFIXES:
        if tok.endswith(p):
            base = tok[: -len(p)]
            if len(base) >= 2:
                return base
    return None


# ── Type-hint boost map ────────────────────────────────────────────────────
# Queries mentioning these keywords get a 3x BM25 weight for matching
# artifact types. e.g. "AI 보안 칼럼" boosts column artifacts ahead of
# generic catch-all docs.
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


def detect_query_type(q: str) -> Optional[str]:
    """Return artifact-type hint implied by the query, or None.

    Token-based matching (NOT substring) to avoid false positives like
    "칼럼이나" (= column-or) triggering the column boost for non-column queries.
    """
    tokens = set(re.findall(r"[A-Za-z0-9가-힣]+", q.lower()))
    for keyword, atype in _TYPE_HINT_MAP.items():
        if keyword in tokens:
            return atype
    return None


# ── Project-hint boost map ─────────────────────────────────────────────────
# When a query names a project explicitly, boost artifacts under that path.
# Solves "동아일보 관련 칼럼이나 제안" → products/donga-roi class of failures
# where the project nickname isn't in the document body verbatim.
_PROJECT_HINT_MAP: dict[str, str] = {
    "donga": "donga", "동아": "donga", "동아일보": "donga",
    "academy": "ai-architect-academy", "아카데미": "ai-architect-academy",
    "curator": "ai-curator", "큐레이터": "ai-curator",
    "simulacra": "simulacra", "시뮬라크라": "simulacra",
    "roadmap": "hypeproof-roadmap", "로드맵": "hypeproof-roadmap",
    "validation": "sediment/VALIDATION", "validator": "sediment/VALIDATION",
}


def detect_project_path(q: str) -> str:
    """Return a substring of `artifacts.ref` implied by the query, or ''."""
    ql = q.lower()
    for kw, path in _PROJECT_HINT_MAP.items():
        if kw in ql:
            return path
    return ""


def slug_regex(q: str) -> str:
    """POSIX regex of alphanumeric query tokens (>=3 chars) for filename match.

    A token in the query that appears in ``a.slug`` or the last segment of
    ``a.ref`` (e.g. "VALIDATION_PLAN" matches "validation"; "2026Q2" matches
    "2026") is a near-certain signal of intent. Boost those hits 2x to
    overcome BM25 plateaus on documents that don't repeat their own name in
    the body. Returns a sentinel that never matches when no tokens exist
    (keeps the asyncpg parameter type stable instead of NULL).
    """
    tokens = re.findall(r"[A-Za-z0-9]{3,}", q)
    if not tokens:
        return "___NEVER___"
    return "(" + "|".join(re.escape(t.lower()) for t in tokens) + ")"


def build_ts_or_query(q: str) -> str:
    """Tokenize free-text query into an OR-joined to_tsquery expression.

    plainto_tsquery uses AND between terms — too strict for offline mode
    where embedding API is unavailable and vector similarity isn't a fallback.
    English stop-words filtered (see ``_STOP_WORDS``); Korean tokens always
    kept (rare + meaningful + to_tsquery('simple', ...) won't dedupe them).
    Mixed Latin+Korean tokens emit both forms — base + Latin-only — so they
    match ts_vectors that stored the term without Korean postfix.
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
            stripped = strip_korean_particles(t_lower)
            if stripped is not None:
                _add(stripped)
            latin_only = re.sub(r"[가-힣]+", "", t_lower)
            if latin_only and len(latin_only) >= 2 and latin_only not in _STOP_WORDS:
                _add(latin_only)
        elif t_lower not in _STOP_WORDS:
            _add(t_lower)

    if not result:
        return ""
    return " | ".join(result)


def is_zero_vector(vec: Sequence[float], tol: float = 1e-9) -> bool:
    """True if ``vec`` is the zero vector that ``embed_one()`` returns in
    offline mode (no OpenAI/Gemini key set).

    Centralized 2026-05-23 (WO-7). Previously the check ``not any(abs(x) > 1e-9 for x in qvec)``
    was duplicated across library.py + lab_curator_graph.py while
    workspace_mcp.py omitted it entirely (LEARNINGS-class recurrence —
    REPORT.md HIGH). Every caller of ``embed_one`` MUST run this guard
    BEFORE letting the vector flow into cosine distance (which yields NaN
    on zero divisors and silently returns empty results to the user).
    """
    return not any(abs(x) > tol for x in vec)
