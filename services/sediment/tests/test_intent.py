"""Regression tests for node_router intent classification.

Covers the priority order: meta > member > decision > library.
Critical edge case: queries containing BOTH a meta keyword and a library keyword
must route to meta (e.g. "칼럼 몇 개?" means "how many columns" — a count query,
not a content retrieval query).
"""
import asyncio

from applications.sediment_langgraph.graphs.lab_curator_graph import node_router


def _route(query: str) -> str:
    state = {"query": query}
    result = asyncio.run(node_router(state))
    return result["intent"]


def test_meta_how_many_english():
    assert _route("How many columns total?") == "meta"


def test_meta_ko_spacing():
    # "몇 개" (spaced) — Korean for "how many"
    assert _route("칼럼이 몇 개야?") == "meta"


def test_meta_ko_no_spacing():
    # "몇개" (no space) — alternate spelling
    assert _route("몇개 있어?") == "meta"


def test_meta_compound_meta_and_library_keyword():
    # The bug that motivated this fix: "column" is a substring of "columns",
    # and "칼럼" is a library keyword. Without meta-first priority, this would
    # route to library.
    assert _route("칼럼 몇 개?") == "meta"
    assert _route("총 칼럼 수") == "meta"


def test_library_column_no_meta_signal():
    # Pure library query — must not be hijacked by meta despite containing "column"
    assert _route("Find me a column about mirror loop") == "library"


def test_member_routing():
    assert _route("Who is Ryan?") == "member"


def test_decision_routing():
    assert _route("What action items came out of last week decisions?") == "decision"


# ---------------------------------------------------------------------------
# sediment#157 — superlative vs period in the freshness detector
# ---------------------------------------------------------------------------
# `_FRESHNESS_KEYWORDS` mixed two grammatically different things and treated
# them alike, so a date qualifier could hijack a question that was not about
# dates. The distinction is what the query is ASKING for:
#
#   superlative ("the newest X")  → freshness IS the question
#   period      ("...last week")  → a filter on some other question


def test_superlative_still_beats_an_explicit_decision_ask():
    """Pinned by test_ask_intent_golden too, and easy to break while fixing
    the period case: "the latest decision" really is a freshness query."""
    assert _route("최신 결정") == "freshness"
    assert _route("latest decisions") == "freshness"


def test_a_bare_period_still_means_freshness():
    """With nothing else to go on, a period qualifier IS the question."""
    assert _route("yesterday's notes") == "freshness"
    assert _route("어제 무슨 일 있었어?") == "freshness"
    assert _route("what happened last week?") == "freshness"


def test_a_period_yields_to_an_explicit_decision_ask():
    """The bug: freshness returns artifacts ordered by date. These queries are
    asking for decisions and actions, with the date merely narrowing them."""
    assert _route("what did we decide last week? any action items") == "decision"
    assert _route("어제 결정된 거 뭐야") == "decision"


def test_decision_keyword_parity_between_korean_and_english():
    """Korean "결정" routed to the decision handler while English "decision"
    routed to a content browse — purely because of which keyword list it had
    been dropped into."""
    assert _route("결정 사항 보여줘") == "decision"
    assert _route("show me the decisions") == "decision"
