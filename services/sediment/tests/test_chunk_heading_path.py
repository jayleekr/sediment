"""sediment#137 — chunk heading_path must survive the round trip.

`lab_lib.chunker` has always computed a heading breadcrumb for each chunk, but
the ingester dropped it at the INSERT and the column did not exist, so every
citation resolved only to "somewhere in this document".

These are source-level contract tests (no DB required, like
test_decision_provenance_contract.py): they pin the write path, the column, and
every read path that surfaces citations. If a new retrieval path is added and
forgets heading_path, the corresponding assertion here should be extended —
that is the point.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lab_lib.chunker import chunk_markdown

REPO = Path(__file__).resolve().parents[3]
SVC = REPO / "services" / "sediment"


def _read(rel: str) -> str:
    return (SVC / rel).read_text()


# ---------------------------------------------------------------------------
# 1. The chunker still produces breadcrumbs (the value being persisted)
# ---------------------------------------------------------------------------

def test_chunker_emits_heading_path():
    md = (
        "# Design\n\n"
        "Intro paragraph.\n\n"
        "## Retrieval\n\n"
        "Hybrid BM25 and vector search over chunks.\n"
    )
    chunks = chunk_markdown(md)
    assert chunks, "fixture should produce at least one chunk"
    paths = [c.heading_path for c in chunks]
    assert any(p for p in paths), f"no heading_path produced: {paths!r}"
    assert any("Retrieval" in (p or "") for p in paths), paths


# ---------------------------------------------------------------------------
# 2. Migration 004 adds the column, idempotently
# ---------------------------------------------------------------------------

def test_migration_004_adds_heading_path_column():
    sql = (REPO / "infra" / "migrations" / "004_chunk_heading_path.sql").read_text()
    assert "ALTER TABLE chunks" in sql
    assert "ADD COLUMN IF NOT EXISTS heading_path TEXT" in sql
    # Every migration records itself — apply_migrations.py does not track it.
    assert "INSERT INTO schema_migrations(name) VALUES ('004_chunk_heading_path')" in sql


# ---------------------------------------------------------------------------
# 3. The ingester writes it
# ---------------------------------------------------------------------------

def test_ingester_persists_heading_path():
    src = _read("applications/vault_ingester/main.py")
    assert "INSERT INTO chunks (tenant_id, artifact_id, seq, content, heading_path, embedding)" in src
    assert '"hpath": c.heading_path or None' in src


# ---------------------------------------------------------------------------
# 4. Every citation-bearing read path selects it
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rel,occurrences", [
    # BM25-only CTE + hybrid join-back
    ("applications/sediment_platform/routers/library.py", 2),
    # offline BM25 select + hybrid join-back
    ("applications/sediment_langgraph/graphs/lab_curator_graph.py", 2),
    # MCP vault_search: offline select + hybrid join-back
    ("lab_platform/mcp_servers/workspace_mcp.py", 2),
])
def test_read_paths_select_heading_path(rel: str, occurrences: int):
    src = _read(rel)
    assert src.count("heading_path") >= occurrences, (
        f"{rel} appears to have a retrieval path that drops heading_path"
    )


def test_hybrid_paths_join_chunks_by_pk():
    """The RRF CTEs group by chunk identity, so heading_path is joined back
    from chunks rather than threaded through the GROUP BY. Pin that shape —
    adding it to the CTEs instead would silently change the fused grouping.
    """
    for rel in (
        "applications/sediment_platform/routers/library.py",
        "applications/sediment_langgraph/graphs/lab_curator_graph.py",
        "lab_platform/mcp_servers/workspace_mcp.py",
    ):
        assert "JOIN chunks ch ON ch.id = f.id" in _read(rel), rel
