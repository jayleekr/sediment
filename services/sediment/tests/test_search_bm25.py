"""Regression test for BM25 offline-mode per-artifact dedup.

The /search endpoint's offline path (zero-vector branch) MUST return at most
one chunk per artifact_id, picking the highest-scoring chunk per artifact.
This prevents a single large artifact (e.g. SPEC.md with 3810 chunks) from
dominating top-K results and crowding out other relevant artifacts.

See: P1-GOLDEN-RAG-01-dedup work-order, library.py /search offline branch.

Implementation note: the two invariants (per-artifact dedup correctness +
LIMIT-as-artifact-count semantics) are verified inside a single test function.
Running them as two separate @pytest.mark.asyncio tests in this module triggers
SQLAlchemy/asyncpg pool teardown across pytest's per-test event-loops, producing
spurious "Event loop is closed" errors during cleanup (same infrastructure
limitation that affects test_rls.py::test_rls_isolates_chunks). Merging the
two phases avoids that without weakening coverage.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text

from lab_lib.db import app_session, service_session


# Shared SQL — exactly mirrors library.py /search offline path.
DEDUP_BM25_SQL = """
WITH raw AS (
    SELECT c.id::text AS chunk_id, c.artifact_id::text, c.seq, c.content,
           ts_rank(c.tsv, to_tsquery('simple', :tsq))::float AS score,
           a.ref, a.type, a.date, a.slug
    FROM chunks c JOIN artifacts a ON a.id = c.artifact_id
    WHERE c.tsv @@ to_tsquery('simple', :tsq)
),
deduped AS (
    SELECT DISTINCT ON (artifact_id)
           chunk_id, artifact_id, seq, content, score, ref, type, date, slug
    FROM raw
    ORDER BY artifact_id, score DESC
)
SELECT chunk_id, artifact_id, seq, content, score, ref, type, date, slug
FROM deduped
ORDER BY score DESC LIMIT :limit;
"""


async def _lab_tenant_id() -> str:
    async with service_session() as s:
        r = (await s.execute(text("SELECT id FROM tenants WHERE slug='hypeproof-lab'"))).first()
        assert r, "Run `make seed` first."
        return str(r[0])


# Only THIS test needs Postgres — the tokenizer/particle tests below are pure
# logic and must keep running without one. Marked per-test rather than
# per-module for that reason; the module-level marker that was supposed to
# cover it lived in conftest.py, where pytest ignores it (sediment#154).
@pytest.mark.skipif(os.environ.get("SKIP_DB") == "1", reason="DB not available")
@pytest.mark.asyncio
async def test_bm25_offline_dedup_invariants():
    """Verifies two invariants of the offline BM25 path:

    Phase 1: DISTINCT ON (artifact_id) returns at most one chunk per artifact,
             and the returned chunk is the HIGHEST-scoring chunk for that
             artifact. Seed: artifact A with 3 chunks (different marker counts),
             artifact B with 1 chunk. Assert: 2 rows back, A's row is the seq
             with the most marker mentions.

    Phase 2: LIMIT N caps the artifact count (not the chunk count). Seed: 5
             artifacts each with one matching chunk. Assert: LIMIT 3 returns
             exactly 3 distinct artifacts.

    Both phases use UUID-derived markers so the test is hermetic against
    whatever corpus is currently seeded in the dev DB.
    """
    tid = await _lab_tenant_id()

    # ------------------------------------------------------------------
    # Phase 1: per-artifact max-score dedup
    # ------------------------------------------------------------------
    marker1 = f"dedupmarker{uuid.uuid4().hex[:10]}"
    ref_a = f"test/dedup-a-{uuid.uuid4().hex[:6]}.md"
    ref_b = f"test/dedup-b-{uuid.uuid4().hex[:6]}.md"

    # ------------------------------------------------------------------
    # Phase 2: LIMIT caps artifact count
    # ------------------------------------------------------------------
    marker2 = f"limitmarker{uuid.uuid4().hex[:10]}"
    p2_aids: list[str] = []

    aid_a: str = ""
    aid_b: str = ""

    try:
        # ---- Setup phase 1
        async with service_session() as s:
            await s.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tid})

            ra = (await s.execute(text("""
                INSERT INTO artifacts (tenant_id, ref, type, body, frontmatter)
                VALUES (:tid, :ref, 'note', :body, '{}'::jsonb)
                ON CONFLICT (tenant_id, ref) DO UPDATE SET body = EXCLUDED.body, updated_at = now()
                RETURNING id
            """), {"tid": tid, "ref": ref_a, "body": f"{marker1} test artifact A"})).first()
            aid_a = str(ra[0])

            rb = (await s.execute(text("""
                INSERT INTO artifacts (tenant_id, ref, type, body, frontmatter)
                VALUES (:tid, :ref, 'note', :body, '{}'::jsonb)
                ON CONFLICT (tenant_id, ref) DO UPDATE SET body = EXCLUDED.body, updated_at = now()
                RETURNING id
            """), {"tid": tid, "ref": ref_b, "body": f"{marker1} test artifact B"})).first()
            aid_b = str(rb[0])

            await s.execute(
                text("DELETE FROM chunks WHERE artifact_id IN (:a, :b)"),
                {"a": aid_a, "b": aid_b},
            )

            # Artifact A: three chunks — seq=2 has 3 marker mentions (highest score),
            # seq=1 has 1 mention, seq=3 has 2 mentions. tsv is auto-populated by
            # the chunks_tsv_trigger BEFORE INSERT trigger (see init.sql).
            chunks_a = [
                (1, f"{marker1} appears once in this chunk."),
                (2, f"{marker1} {marker1} {marker1} dense chunk content."),
                (3, f"{marker1} {marker1} mid-density chunk."),
            ]
            for seq, content in chunks_a:
                await s.execute(text("""
                    INSERT INTO chunks (tenant_id, artifact_id, seq, content)
                    VALUES (:tid, :aid, :seq, :content)
                """), {"tid": tid, "aid": aid_a, "seq": seq, "content": content})

            await s.execute(text("""
                INSERT INTO chunks (tenant_id, artifact_id, seq, content)
                VALUES (:tid, :aid, :seq, :content)
            """), {"tid": tid, "aid": aid_b, "seq": 1, "content": f"{marker1} appears in artifact B."})

            # ---- Setup phase 2 (same session)
            for i in range(5):
                ref = f"test/limit-{i}-{uuid.uuid4().hex[:6]}.md"
                r = (await s.execute(text("""
                    INSERT INTO artifacts (tenant_id, ref, type, body, frontmatter)
                    VALUES (:tid, :ref, 'note', :body, '{}'::jsonb)
                    ON CONFLICT (tenant_id, ref) DO UPDATE SET body = EXCLUDED.body, updated_at = now()
                    RETURNING id
                """), {"tid": tid, "ref": ref, "body": f"{marker2} doc {i}"})).first()
                aid = str(r[0])
                p2_aids.append(aid)
                await s.execute(
                    text("DELETE FROM chunks WHERE artifact_id = :aid"),
                    {"aid": aid},
                )
                await s.execute(text("""
                    INSERT INTO chunks (tenant_id, artifact_id, seq, content)
                    VALUES (:tid, :aid, 1, :content)
                """), {"tid": tid, "aid": aid, "content": f"{marker2} document number {i}"})

        # ---- Assert phase 1
        async with app_session(tid) as s:
            r = await s.execute(text(DEDUP_BM25_SQL), {"tsq": marker1, "limit": 8})
            rows = [dict(row._mapping) for row in r]

        rows = [r for r in rows if r["artifact_id"] in (aid_a, aid_b)]
        artifact_ids = [r["artifact_id"] for r in rows]
        assert len(rows) == 2, (
            f"Phase 1: expected 2 rows (one per artifact), got {len(rows)}: {rows}"
        )
        assert len(set(artifact_ids)) == 2, (
            f"Phase 1: expected unique artifact_ids, got {artifact_ids}"
        )
        row_a = next(r for r in rows if r["artifact_id"] == aid_a)
        assert row_a["seq"] == 2, (
            "Phase 1: dedup must pick highest-score chunk per artifact, "
            f"expected seq=2 (3 marker mentions), got seq={row_a['seq']}"
        )

        # ---- Assert phase 2
        async with app_session(tid) as s:
            r = await s.execute(text(DEDUP_BM25_SQL), {"tsq": marker2, "limit": 3})
            rows = [dict(row._mapping) for row in r]

        rows = [r for r in rows if r["artifact_id"] in p2_aids]
        assert len(rows) == 3, f"Phase 2: LIMIT 3 must return 3 rows, got {len(rows)}"
        seen_aids = {r["artifact_id"] for r in rows}
        assert len(seen_aids) == 3, (
            f"Phase 2: LIMIT 3 must return 3 DISTINCT artifacts, "
            f"got {len(seen_aids)}: {seen_aids}"
        )
    finally:
        # Hermetic cleanup — single session keeps pool teardown in one event loop.
        async with service_session() as s:
            await s.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tid})
            cleanup_aids = [a for a in [aid_a, aid_b, *p2_aids] if a]
            for aid in cleanup_aids:
                await s.execute(
                    text("DELETE FROM chunks WHERE artifact_id = :aid"),
                    {"aid": aid},
                )
                await s.execute(
                    text("DELETE FROM artifacts WHERE id = :aid"),
                    {"aid": aid},
                )


# ---------------------------------------------------------------------------
# Unit tests: Korean particle stripping in _build_ts_or_query
# P1-RAG-KO-particle — pure Python, no DB needed.
# ---------------------------------------------------------------------------

def test_build_ts_or_query_strips_korean_particles():
    """_build_ts_or_query must emit base forms alongside particle-attached tokens.

    "라이언이 4월에 쓴 mirror-loop 칼럼" was previously tokenized as:
      라이언이 | 4월에 | 쓴 | mirror | loop | 칼럼
    with 0 DB hits for 라이언이 and 4월에 because the tsvector stores base forms.

    After the fix, base forms must be present in the OR query too.
    """
    from applications.sediment_langgraph.graphs.lab_curator_graph import (
        _build_ts_or_query,
    )

    result = _build_ts_or_query("라이언이 4월에 쓴 mirror-loop 칼럼")
    tokens = set(result.split(" | "))

    assert "라이언" in tokens, f"Base form '라이언' missing from: {result}"
    assert "4월" in tokens, f"Base form '4월' missing from: {result}"
    assert "라이언이" in tokens, f"Original '라이언이' should still be present: {result}"
    assert "4월에" in tokens, f"Original '4월에' should still be present: {result}"
    assert "칼럼" in tokens, f"'칼럼' (no particle) missing: {result}"


def test_build_ts_or_query_strips_particles_library_router():
    """library.py's _build_ts_or_query must behave identically to langgraph's."""
    from applications.sediment_platform.routers.library import (
        _build_ts_or_query as lib_build,
    )

    result = lib_build("라이언이 4월에 쓴 mirror-loop 칼럼")
    tokens = set(result.split(" | "))

    assert "라이언" in tokens, f"library.py: base form '라이언' missing from: {result}"
    assert "4월" in tokens, f"library.py: base form '4월' missing from: {result}"
    assert "라이언이" in tokens
    assert "4월에" in tokens


import pytest

@pytest.mark.parametrize("particle_tok,expected_base", [
    ("라이언이", "라이언"),
    ("4월에", "4월"),
    ("라이언의", "라이언"),
    ("라이언을", "라이언"),
    ("라이언는", "라이언"),
    ("라이언과", "라이언"),
    ("라이언으로", "라이언"),
    ("에서", None),
    ("라이언에서", "라이언"),
])
def test_strip_korean_particles_cases(particle_tok, expected_base):
    # sediment#156: this imported `_strip_korean_particles` from
    # lab_curator_graph, which WO-7 (2026-05-23) removed when it extracted the
    # helper to search_utils. Every case had raised ImportError since —
    # meaning the regression guard for the Korean-particle stripping that
    # P1-RAG-KO-particle depends on had not actually run in months. Import the
    # canonical location, so a future extraction breaks it loudly instead.
    from lab_lib.search_utils import strip_korean_particles

    result = strip_korean_particles(particle_tok)
    assert result == expected_base, (
        f"_strip_korean_particles({particle_tok!r}) = {result!r}, expected {expected_base!r}"
    )
