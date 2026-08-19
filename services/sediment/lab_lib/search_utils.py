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
  - ``slug_regex``                tokens regex for filename match boost
  - ``build_ts_or_query``         the OR-joined to_tsquery expression
  - ``prefer_bm25_first``         skip slow online embeddings for lexical-heavy queries
  - ``is_zero_vector``            offline-mode embedding detector

When you find yourself copy-pasting any of the above into a new retrieval
path, STOP and import from here instead. The triplication LEARNINGS warned
about already cost one recurrence; the next will too.

2026-08-02 (sediment#139): the type-hint and project-hint maps LEFT this module
for ``lab_lib.aliases`` / the ``tenant_aliases`` table. They were not shared
logic — they were one workspace's proper nouns (newspaper names, internal
project nicknames) hardcoded into a multi-tenant retrieval path. What remains
here is genuinely tenant-independent: stop-words, Korean particle stripping,
tokenization.

Keep it that way. ``tests/test_tenant_aliases.py`` fails this module if a
tenant-specific proper noun reappears anywhere in it — including in a comment,
which is why this paragraph names none.
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


def prefer_bm25_first(q: str) -> bool:
    """True when lexical retrieval should run before external embeddings.

    Korean entity/curriculum questions and long multi-token questions have
    strong BM25 signal in this corpus, while the external embedding call can
    dominate latency or hang the user-facing "thinking" state. This is a
    fail-fast guard for sediment#58: return a grounded lexical answer quickly
    instead of blocking on vector search.
    """
    tokens = re.findall(r"[A-Za-z0-9가-힣_]+", q)
    if any(any("가" <= c <= "힣" for c in tok) for tok in tokens):
        return True
    signal_tokens = [
        tok.lower()
        for tok in tokens
        if len(tok) >= 2 and tok.lower() not in _STOP_WORDS
    ]
    return len(signal_tokens) >= 4


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
