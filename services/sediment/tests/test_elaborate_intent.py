"""Unit tests for the elaborate intent + length-adaptive prompt.

Triggered by Jay's UX critique 2026-05-22 — "디테일하게 설명해" returned
one sentence about a tangential topic. Root cause: the short follow-up
got re-searched via RAG instead of reusing the prior answer's citations.

These tests verify:
1. Router classifies elaborate keywords correctly (KO + EN)
2. Router does NOT swallow library/member/meta queries
3. Graph wires the elaborate node
4. node_elaborate's SQL filters by tenant + conv_id explicitly (sediment#16)
"""
from __future__ import annotations

import asyncio
import inspect
import re

import pytest

from applications.sediment_langgraph.graphs.sediment_graph import (
    _is_elaborate_query,
    node_router,
    build_graph,
)


# ---------------------------------------------------------------------------
# Router classifies elaborate queries (KO + EN)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query", [
    "디테일하게 설명해",
    "더 자세히",
    "자세히 설명해줘",
    "자세하게 알려줘",
    "구체적으로",
    "상세히 알려줘",
    "explain more",
    "expand on that",
    "in more detail",
    "elaborate please",
    "tell me more",
    "더 알려줘",
    "조금 더 설명해줘",
])
def test_elaborate_keywords_detected(query):
    assert _is_elaborate_query(query.lower()) is True, query


@pytest.mark.parametrize("query", [
    "보아치과 커리큘럼",
    "AI Native 마인드 7종",
    "Ryan 누구야",
    "칼럼 몇 개?",
    "가장 최신 결정",
])
def test_non_elaborate_queries_not_misrouted(query):
    assert _is_elaborate_query(query.lower()) is False, query


async def test_router_routes_elaborate_intent_with_conv():
    state = {"query": "디테일하게 설명해", "conv_id": "test-conv-uuid"}
    result = await node_router(state)
    assert result["intent"] == "elaborate"


async def test_router_falls_back_to_library_without_conv():
    """If somehow elaborate is detected but there's no conv (first message
    in session), don't route to elaborate — fall back to library."""
    state = {"query": "디테일하게 설명해", "conv_id": None}
    result = await node_router(state)
    assert result["intent"] == "library"


async def test_router_still_routes_other_intents():
    cases = [
        ("AI Native 마인드 설명해줘", "library"),
        ("Ryan 누구야", "member"),
        ("칼럼 몇 개", "meta"),
        ("가장 최신 결정", "freshness"),
    ]
    for q, expected in cases:
        result = await node_router({"query": q, "conv_id": "x"})
        assert result["intent"] == expected, f"{q} → {result['intent']}, expected {expected}"


# ---------------------------------------------------------------------------
# Graph wiring
# ---------------------------------------------------------------------------

def test_graph_includes_elaborate_node():
    g = build_graph()
    node_names = set(g.get_graph().nodes.keys())
    assert "elaborate" in node_names


def test_elaborate_node_has_tenant_and_conv_filter():
    """SQL inside node_elaborate must filter both by tenant_id AND conv_id."""
    import importlib
    mod = importlib.import_module("applications.sediment_langgraph.graphs.sediment_graph")
    src = inspect.getsource(mod)
    m = re.search(
        r"async def node_elaborate.*?(?=^(?:async )?def |\Z)",
        src, re.S | re.M,
    )
    assert m, "node_elaborate not found"
    body = m.group(0)
    assert "tenant_id = CAST(:tid AS uuid)" in body, \
        "elaborate query must include explicit tenant filter (sediment#16)"
    assert "conv_id = CAST(:cid AS uuid)" in body, \
        "elaborate query must scope to this conversation only"
    assert "ORDER BY created_at DESC" in body, \
        "elaborate must pick the MOST RECENT prior assistant turn"
    assert "role = 'assistant'" in body, \
        "elaborate picks assistant turns (with citations), not user turns"


# ---------------------------------------------------------------------------
# Defensive: keyword overlap doesn't break existing routing
# ---------------------------------------------------------------------------

def test_elaborate_does_not_swallow_normal_questions():
    """A query containing 'explain' but NOT 'explain more' shouldn't elaborate."""
    cases = [
        "explain the iframe sandbox decision",   # 'explain' alone — library
        "explain Ryan's expertise",              # 'explain' + member name → member
    ]
    for q in cases:
        assert _is_elaborate_query(q.lower()) is False, q
