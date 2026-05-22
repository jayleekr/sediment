"""Unit tests for the freshness intent + node (sediment#16 #4)."""
from __future__ import annotations

import asyncio
import inspect
import re

import pytest

from applications.sediment_langgraph.graphs.lab_curator_graph import (
    _is_freshness_query,
    _detect_freshness_type,
    node_router,
    build_graph,
)


# ---------------------------------------------------------------------------
# Router classifies freshness queries correctly
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query", [
    "가장 최신 볼트가 언제꺼야?",
    "최신 리서치가 뭐야",
    "가장 최근 결정",
    "what's the latest research",
    "most recent column",
    "newest meeting note",
    "this week's research",
    "오늘 새로 들어온 거",
    "어제 들어온 글",
])
def test_freshness_keywords_detected(query):
    assert _is_freshness_query(query.lower()) is True, query


@pytest.mark.parametrize("query", [
    "AI Native 마인드 7종 설명해줘",
    "Ryan은 누구인가",
    "iframe sandbox 결정 배경",
    "칼럼 몇 개?",
])
def test_non_freshness_queries_not_misrouted(query):
    assert _is_freshness_query(query.lower()) is False, query


async def test_router_routes_freshness_intent():
    state = {"query": "가장 최신 daily research가 언제꺼야?"}
    result = await node_router(state)
    assert result["intent"] == "freshness"


async def test_router_still_routes_other_intents():
    """The freshness keywords must NOT swallow library/member/meta queries."""
    cases = [
        ("AI Native 마인드 설명해줘", "library"),
        ("Ryan 누구야", "member"),
        ("칼럼 몇 개", "meta"),
    ]
    for query, expected in cases:
        result = await node_router({"query": query})
        assert result["intent"] == expected, f"{query} → {result['intent']}, expected {expected}"


# ---------------------------------------------------------------------------
# Type detector — pulls artifact type from freshness query
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query,expected", [
    ("최신 리서치", "research"),
    ("latest research", "research"),
    ("가장 최근 결정", "decision"),
    ("latest decision", "decision"),
    ("최신 회의록", "meeting"),
    ("latest meeting note", "meeting"),
    ("가장 최근 칼럼", "column"),
    ("최신 볼트", None),   # ambiguous — no type hint
])
def test_detect_freshness_type(query, expected):
    assert _detect_freshness_type(query) == expected, query


# ---------------------------------------------------------------------------
# Graph structure — freshness node is wired
# ---------------------------------------------------------------------------

def test_graph_includes_freshness_node():
    """build_graph() must register 'freshness' as a node + as a router edge."""
    g = build_graph()
    # langgraph's compiled graph exposes nodes via .nodes (dict-like)
    node_names = set(g.get_graph().nodes.keys())
    assert "freshness" in node_names, f"freshness node missing; have: {node_names}"


def test_freshness_node_has_tenant_filter():
    """SQL inside node_freshness_lookup must filter by tenant explicitly
    (sediment#16 defense-in-depth)."""
    import importlib
    mod = importlib.import_module("applications.sediment_langgraph.graphs.lab_curator_graph")
    src = inspect.getsource(mod)
    # Pull the whole function — up to the next top-level def/async def
    m = re.search(
        r"async def node_freshness_lookup.*?(?=^(?:async )?def |\Z)",
        src, re.S | re.M,
    )
    assert m, "node_freshness_lookup not found"
    body = m.group(0)
    assert "tenant_id = CAST(:tid AS uuid)" in body, \
        "freshness query must include explicit tenant filter (sediment#16)"
    assert "ORDER BY" in body and "DESC" in body, \
        "freshness query must ORDER BY date DESC"
