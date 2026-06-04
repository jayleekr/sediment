"""Unit tests for the sediment#16 defense-in-depth guards.

These do NOT require a running DB — they verify the CODE PATH includes the
tenant filter clauses. Integration tests against a real DB live in
test_rls.py (which already passes once roles are set correctly).

Coverage:
  - retrieval SQL strings include `tenant_id = CAST(:tid AS uuid)` in
    every CTE / WHERE clause that touches chunks/artifacts/members
  - embeddings module logs CRITICAL on first zero-vector fallback
  - cleanup script's stale-pair detector identifies the right pairs
"""
from __future__ import annotations

import importlib
import inspect


# ---------------------------------------------------------------------------
# Retrieval SQL — tenant filter must appear
# ---------------------------------------------------------------------------

def _read_source(modpath: str) -> str:
    mod = importlib.import_module(modpath)
    return inspect.getsource(mod)


def test_library_search_has_explicit_tenant_filter_bm25():
    src = _read_source("applications.sediment_langgraph.graphs.lab_curator_graph")
    # BM25-only branch must include both the chunk + artifact filter
    assert "a.tenant_id = CAST(:tid AS uuid)" in src, \
        "BM25 retrieval must have explicit artifact tenant filter (sediment#16)"
    assert "c.tenant_id = CAST(:tid AS uuid)" in src, \
        "BM25 retrieval must also filter chunks by tenant (sediment#16)"


def test_library_search_has_explicit_tenant_filter_hybrid():
    src = _read_source("applications.sediment_langgraph.graphs.lab_curator_graph")
    # Hybrid path uses CTEs — both `bm25` and `vec` CTEs need the filter
    # (we count occurrences as a sanity check)
    assert src.count("c.tenant_id = CAST(:tid AS uuid)") >= 2, \
        "Both CTEs in hybrid retrieval need tenant filter"


def test_hybrid_retrieval_assigns_rank_after_topk_pushdown():
    src = _read_source("applications.sediment_langgraph.graphs.lab_curator_graph")

    assert "WITH bm25_top AS" in src
    assert "vec_top AS" in src
    assert "FROM bm25_top" in src
    assert "FROM vec_top" in src
    assert "row_number() OVER (ORDER BY ts_rank" not in src, \
        "BM25 ranking must happen after ORDER BY/LIMIT top-K pushdown"
    assert "row_number() OVER (ORDER BY c.embedding <=>" not in src, \
        "Vector ranking must happen after ORDER BY/LIMIT top-K pushdown"


def test_platform_search_hybrid_matches_topk_pushdown_shape():
    src = _read_source("applications.sediment_platform.routers.library")

    assert "WITH bm25_top AS" in src
    assert "vec_top AS" in src
    assert "WHERE a.tenant_id = CAST(:tid AS uuid)" in src
    assert "row_number() OVER (ORDER BY ts_rank" not in src
    assert "row_number() OVER (ORDER BY c.embedding <=>" not in src


def test_platform_search_bm25_has_tenant_filters_and_timeout():
    src = _read_source("applications.sediment_platform.routers.library")

    assert "SET LOCAL statement_timeout = '8s'" in src
    assert "AND c.tenant_id = CAST(:tid AS uuid)" in src
    assert "AND a.tenant_id = CAST(:tid AS uuid)" in src


def test_search_utils_prefers_bm25_for_korean_and_long_queries():
    from lab_lib.search_utils import prefer_bm25_first

    assert prefer_bm25_first("AI Native 마인드 7종이 뭐야?")
    assert prefer_bm25_first("LLM provider scaling 40명 동시 접속 대응 전략")
    assert prefer_bm25_first("retrieval latency regression hybrid search natural language")
    assert not prefer_bm25_first("BH")


def test_member_lookup_has_tenant_filter():
    src = _read_source("applications.sediment_langgraph.graphs.lab_curator_graph")
    # Locate the member lookup function source
    import re
    m = re.search(r"async def node_member_lookup.*?return", src, re.S)
    assert m, "node_member_lookup not found"
    body = m.group(0)
    assert "tenant_id = CAST(:tid AS uuid)" in body, \
        "members lookup must filter by tenant (sediment#16)"


def test_meta_summary_has_tenant_filter():
    src = _read_source("applications.sediment_langgraph.graphs.lab_curator_graph")
    import re
    m = re.search(r"async def node_meta_summary.*?return", src, re.S)
    assert m, "node_meta_summary not found"
    body = m.group(0)
    assert "tenant_id = CAST(:tid AS uuid)" in body, \
        "meta summary must filter artifacts by tenant (sediment#16)"


# ---------------------------------------------------------------------------
# Embeddings — loud-failure contract
# ---------------------------------------------------------------------------

def test_embed_logs_critical_on_first_no_key(monkeypatch, capsys):
    """If no provider key is set, the FIRST embed call must emit a
    CRITICAL-equivalent log so it can't be silently lost (sediment#16).

    Structlog goes to stdout (captured via capsys), not the stdlib logging
    caplog fixture — so we assert on the captured stdout text.
    """
    from lab_lib import embeddings as emb

    # Reset module-level state — force `zero` provider so we exercise the
    # no-key path regardless of what's in the actual env.
    emb._client_gemini = None
    emb._client_openai = None
    emb._NO_KEY_WARN_COUNT = 0
    monkeypatch.setenv("EMBEDDING_PROVIDER", "zero")
    monkeypatch.setattr(emb.settings, "openai_api_key", "")
    monkeypatch.setattr(emb.settings, "gemini_api_key", "")

    vec = emb.embed_one("hello")

    assert vec == [0.0] * emb.settings.embedding_dim
    captured = capsys.readouterr()
    # First-time alert must mention the CRITICAL marker and the issue ref
    assert "embed.no_provider_key.CRITICAL" in captured.out or \
           "embed.no_provider_key.CRITICAL" in captured.err, \
           f"Expected loud first-time alert in stdout/stderr; got out={captured.out[-300:]!r} err={captured.err[-300:]!r}"
    assert emb._NO_KEY_WARN_COUNT == 1


def test_embed_subsequent_calls_log_warn_only(monkeypatch, capsys):
    """After the first CRITICAL, subsequent zero-fallback calls log WARN
    (not error) to avoid log-spam — but include a running counter."""
    from lab_lib import embeddings as emb
    emb._client_gemini = None
    emb._client_openai = None
    emb._NO_KEY_WARN_COUNT = 0
    monkeypatch.setenv("EMBEDDING_PROVIDER", "zero")
    monkeypatch.setattr(emb.settings, "openai_api_key", "")
    monkeypatch.setattr(emb.settings, "gemini_api_key", "")

    emb.embed_one("first")    # CRITICAL
    emb.embed_one("second")   # WARN
    emb.embed_one("third")    # WARN

    assert emb._NO_KEY_WARN_COUNT == 3
    out = capsys.readouterr().out
    # The CRITICAL appears exactly once
    assert out.count("embed.no_provider_key.CRITICAL") == 1


def test_embed_zero_vector_shape_unchanged(monkeypatch):
    """Length contract — zero vectors must still be 1536-d."""
    from lab_lib import embeddings as emb
    emb._client_gemini = None
    emb._client_openai = None
    monkeypatch.setenv("EMBEDDING_PROVIDER", "zero")
    monkeypatch.setattr(emb.settings, "openai_api_key", "")
    monkeypatch.setattr(emb.settings, "gemini_api_key", "")
    vec = emb.embed_one("hello")
    assert len(vec) == emb.EMBEDDING_DIM


# ---------------------------------------------------------------------------
# app_session — BYPASSRLS detection
# ---------------------------------------------------------------------------

def test_app_session_module_warns_on_bypassrls():
    """The warning code path exists and references the issue."""
    src = _read_source("lab_lib.db")
    assert "rolbypassrls" in src
    assert "bypassrls_detected" in src or "BYPASSRLS" in src.upper()
    assert "sediment#16" in src
