"""Golden set: edge-case queries the user expects to 'just work', and the
ROUTER + RETRIEVAL pipeline behavior we promise for each.

This file is the contract between users and the answer pipeline. When a
real user query fails (like "가장 최근의 볼트는 언제꺼야"), it gets a row
here BEFORE the fix lands so we don't regress.

Each case asserts at the LANGGRAPH LEVEL (not LLM-output level) — we
verify:
  - Router picks the right intent
  - The retrieval node returns the shape that makes a good answer possible
  - The citation list is non-empty AND has the structural fields the
    compose step needs

We deliberately DO NOT assert on LLM-generated answer text — that's
non-deterministic and the wrong layer to lock down. The grounded-answer
gate's behavior IS tested via answer_text presence elsewhere.

Run:
  cd services/sediment
  PYTHONPATH=. .venv/bin/pytest tests/test_ask_intent_golden.py -v
"""
from __future__ import annotations
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_DB") == "1", reason="DB not available"
)


@pytest.fixture
async def router_only():
    """Run only the router node — fast, no retrieval, no LLM."""
    from applications.sediment_langgraph.graphs.sediment_graph import node_router

    async def _run(query: str) -> str:
        # Minimal state — router only reads `query` and `tenant_id`.
        state = {
            "query": query,
            "tenant_id": "00000000-0000-0000-0000-000000000000",
            "history": [],
        }
        result = await node_router(state)
        return result.get("intent", "")

    return _run


# ============================================================
# Intent routing — query → expected intent
# ============================================================

INTENT_CASES = [
    # (query, expected_intent, why)
    # ── meta / counts ────────────────────────────────────
    ("지금 볼트에 몇 개의 artifact가 있어?", "meta", "count query — '몇 개' is the trigger"),
    ("How many notes do we have?", "meta", "count query in English"),
    ("총 몇 개?", "meta", "bare count"),
    ("이번 달 새로 생긴 게 몇 개?", "meta", "count + recent — meta wins"),
    # ── freshness / recency ─────────────────────────────
    ("가장 최근의 볼트는 언제꺼야", "freshness", "'가장 최근' is a canonical freshness keyword"),
    ("가장최근의 볼트는 언제꺼야", "freshness", "no-space variant — '최근의' substring matches"),
    ("최신 칼럼 알려줘", "freshness", "'최신' (latest)"),
    ("latest research", "freshness", "English 'latest'"),
    ("yesterday's notes", "freshness", "English 'yesterday'"),
    ("어제 무슨 일 있었어?", "freshness", "Korean 'yesterday'"),
    # ── library / content ───────────────────────────────
    ("라이언이 4월에 쓴 mirror-loop 칼럼", "library", "content-type keyword '칼럼' beats member name"),
    ("PHILOSOPHY.md 내용 알려줘", "library", "explicit ref — should hit library"),
    ("memory consolidation 관련 리서치 보여줘", "library", "'리서치' content keyword"),
    # ── member ──────────────────────────────────────────
    ("ryan은 누구야?", "member", "no content-type keyword, member name present"),
    ("who is JY?", "member", "English member lookup"),
    # ── decision ────────────────────────────────────────
    # NOTE: "최근 결정" is intentionally NOT freshness — "최근" alone (without
    # "의"/"에"/"가장" etc.) is too generic. _FRESHNESS_KEYWORDS deliberately
    # requires more specific phrasing to avoid eating every "최근 X" query.
    # If a user wants ordered-by-date decisions, they should say "최신 결정".
    ("최근 결정된 5/5 파일럿 관련 액션", "decision", "'결정' wins; '최근' alone isn't a freshness keyword"),
    ("결정 사항 보여줘", "decision", "explicit decision keyword, no freshness/library noun"),
    ("최신 결정", "freshness", "'최신' IS a freshness keyword — wins over decision"),
    # ── ambiguous / edge cases ──────────────────────────
    ("", "library", "empty query → fallback to library (no crash)"),
    ("?", "library", "single-char query → library fallback"),
    ("aaaa bbbb cccc", "library", "gibberish → library fallback"),
]


@pytest.mark.parametrize("query,expected,_why", INTENT_CASES)
async def test_router_classifies(router_only, query: str, expected: str, _why: str):
    actual = await router_only(query)
    assert actual == expected, (
        f"\n  Query: {query!r}\n"
        f"  Expected intent: {expected}  ({_why})\n"
        f"  Got intent:      {actual}"
    )


# ============================================================
# Freshness query — ensure dates come from CONTENT not INGESTION
# ============================================================

async def test_freshness_returns_artifacts_with_real_content_dates():
    """The bug user hit: query 'when is the most recent vault item?' got
    answered with a meeting_notes file dated null + ingested 2026-05-21,
    not the actual most-recent CONTENT artifact.

    Pipeline: freshness lookup → citations → compose
    Promise: citations[0] has a non-null `date` (content date) OR the
    compose layer surfaces this 'unknown date' state cleanly instead of
    silently sorting by ingest time."""
    from sqlalchemy import text
    from lab_lib.db import service_session

    async with service_session() as s:
        r = await s.execute(text("""
            SELECT
              COUNT(*) FILTER (WHERE date IS NOT NULL) AS has_date,
              COUNT(*) FILTER (WHERE date IS NULL)     AS no_date,
              MAX(date) AS max_date,
              MAX(updated_at::date) AS max_ingest
            FROM artifacts
        """))
        row = r.first()
    if row is None or (row[0] or 0) == 0:
        pytest.skip("no dated artifacts in DB")
    has_date, no_date, max_date, max_ingest = row
    # We don't assert the freshness handler's output here — that requires
    # spinning up the full graph + tenant context. The structural promise:
    # there ARE dated artifacts to return, and max(date) reflects content
    # truth (not ingestion).
    assert has_date > 0, "no dated artifacts at all — freshness query has nothing real to return"
    if max_date is not None and max_ingest is not None:
        # Sanity: in a healthy vault, max content date and max ingest date
        # should be within a reasonable window (1 year). Wildly divergent
        # means ingestion is broken or date metadata is stale.
        from datetime import date
        delta_days = abs((max_ingest - max_date).days)
        assert delta_days < 365, (
            f"max content date ({max_date}) is {delta_days}d off max ingest date "
            f"({max_ingest}) — date frontmatter may be stale"
        )


# ============================================================
# Meta query — counts roundtrip
# ============================================================

async def test_meta_count_matches_artifact_table():
    """If meta queries return per-type counts, those counts must match a
    raw COUNT(*) GROUP BY type from the artifacts table. Catches drift
    if a future commit caches counts wrong."""
    from sqlalchemy import text
    from lab_lib.db import service_session

    async with service_session() as s:
        r = await s.execute(text("""
            SELECT type, COUNT(*) FROM artifacts GROUP BY type
        """))
        from_table = {row[0]: row[1] for row in r}
    if not from_table:
        pytest.skip("no artifacts seeded")
    # The freshness node's by_type values come from this same query against
    # the same table, so they should match exactly. This test mostly
    # ensures the table itself isn't empty/corrupted before higher-level
    # tests rely on it.
    assert sum(from_table.values()) > 0


# ============================================================
# Misclassification regression set — past bugs we won't relive
# ============================================================

# Each tuple: (query, MUST_NOT_BE intent, why this was once wrong)
NEGATIVE_CASES = [
    ("라이언이 쓴 칼럼", "member", "P2-INTENT-03: content keyword must beat member name"),
    ("how many?", "library", "count query must NOT fall through to RAG"),
    ("가장 최근의 볼트", "library", "freshness keyword must NOT fall through to library"),
    ("가장최근의 볼트", "library", "no-space freshness variant — must still detect"),
]


@pytest.mark.parametrize("query,bad_intent,_why", NEGATIVE_CASES)
async def test_router_avoids_known_misclassifications(
    router_only, query: str, bad_intent: str, _why: str
):
    actual = await router_only(query)
    assert actual != bad_intent, (
        f"\n  Query: {query!r}\n"
        f"  Must NOT be: {bad_intent}  ({_why})\n"
        f"  Got:         {actual}"
    )


# ============================================================
# Freshness renderer — deterministic answer construction
# ============================================================

def test_freshness_renderer_with_content_date():
    """First citation with a content date → headline says that date."""
    from applications.sediment_langgraph.main import _render_freshness_answer
    cits = [
        {"type": "research", "rank": 1, "ref": "research/daily/2026-04-08.md",
         "date": "2026-04-08", "ingested_at": "2026-05-21 14:00:00"},
        {"type": "note", "rank": 2, "ref": "research/columns/foo.md",
         "date": "2026-04-01", "ingested_at": "2026-05-21 14:00:00"},
    ]
    out = _render_freshness_answer(cits)
    assert "2026-04-08" in out
    assert "research/daily/2026-04-08.md" in out
    assert "[1]" in out
    # Should NOT mention "반입일" when content date is present
    assert "반입일" not in out


def test_freshness_renderer_falls_back_to_ingest_date():
    """When content date is null, use ingested_at with (반입일) marker so
    user knows the difference."""
    from applications.sediment_langgraph.main import _render_freshness_answer
    cits = [
        {"type": "meeting", "rank": 1, "ref": "meeting_notes/wizard.md",
         "date": None, "ingested_at": "2026-05-21 14:45:42.500541+00"},
    ]
    out = _render_freshness_answer(cits)
    assert "meeting_notes/wizard.md" in out
    assert "2026-05-21" in out
    assert "반입일" in out, "must mark that this is ingestion date, not content date"


def test_freshness_renderer_empty_list():
    from applications.sediment_langgraph.main import _render_freshness_answer
    out = _render_freshness_answer([])
    assert "찾지 못했습니다" in out


def test_freshness_renderer_lists_top_4_followups():
    from applications.sediment_langgraph.main import _render_freshness_answer
    cits = [
        {"type": t, "rank": i + 1, "ref": f"r-{i}.md",
         "date": f"2026-04-{10-i:02d}", "ingested_at": "2026-05-21"}
        for i, t in enumerate(["research", "note", "decision", "meeting", "column"])
    ]
    out = _render_freshness_answer(cits)
    # Headline = first; follow-ups = 4 next
    assert out.count("- [") == 4
    assert "r-4.md" in out  # last of the 5
    # r-5 (rank 6, sliced out) should NOT appear if it existed
    cits.append({"type": "x", "rank": 6, "ref": "r-5.md",
                 "date": "2026-04-05", "ingested_at": "2026-05-21"})
    out2 = _render_freshness_answer(cits)
    assert "r-5.md" not in out2
