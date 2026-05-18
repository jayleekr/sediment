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
