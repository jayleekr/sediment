"""sediment#141 — conflicting knowledge must survive as knowledge.

#138 stopped a colliding re-distill from erasing the previous body. But a second
author's differing decision still had nowhere to exist as its own claim: same
topic slug, same ref, one page. In a single-author vault a conflict is a mistake
to fix; across people it is the most valuable signal the system has, and the
pipeline reliably destroyed it.

Behavioural tests for the ref-resolution rules (pure logic, no DB) plus
source-level contracts for the schema, the writer and the read paths.
"""
from __future__ import annotations


from pathlib import Path
from unittest.mock import patch

import pytest

from lab_lib.links import EXPANDABLE_KINDS, create_link, expand_with_links

REPO = Path(__file__).resolve().parents[3]
SVC = REPO / "services" / "sediment"


def _read(rel: str) -> str:
    return (SVC / rel).read_text()


# ---------------------------------------------------------------------------
# Ref resolution — the actual behaviour change
# ---------------------------------------------------------------------------

class _FakeRow:
    def __init__(self, d):
        self._mapping = d


class _FakeResult:
    def __init__(self, rows):
        self._rows = [_FakeRow(r) for r in rows]

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, *_a, **_k):
        return _FakeResult(self.rows)

    async def commit(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


async def _resolve_async(siblings, base="decision/use-postgres",
                         src="conv/1", body="B"):
    from scripts import distill

    with patch.object(distill, "service_session", lambda: _FakeSession(siblings)):
        return await distill._resolve_decision_ref("t", base, src, body)


async def test_no_existing_page_uses_the_base_ref():
    ref, conflicts = await _resolve_async([])
    assert ref == "decision/use-postgres"
    assert conflicts == []


async def test_identical_body_reuses_the_existing_page():
    """Same claim reached twice. A second page would be noise, not knowledge."""
    ref, conflicts = await _resolve_async([
        {"id": "a1", "ref": "decision/use-postgres", "body": "B", "source": "conv/9"},
    ])
    assert ref == "decision/use-postgres"
    assert conflicts == []


async def test_same_source_updates_in_place():
    """The re-decide case the original topic-slug scheme was designed for."""
    ref, conflicts = await _resolve_async([
        {"id": "a1", "ref": "decision/use-postgres", "body": "OLD", "source": "conv/1"},
    ], src="conv/1", body="NEW")
    assert ref == "decision/use-postgres"
    assert conflicts == []


async def test_different_source_mints_a_sibling_and_reports_the_conflict():
    """The bug. Before #141 this overwrote conv/9's decision."""
    ref, conflicts = await _resolve_async([
        {"id": "a1", "ref": "decision/use-postgres", "body": "OLD", "source": "conv/9"},
    ], src="conv/1", body="NEW")
    assert ref == "decision/use-postgres--2"
    assert conflicts == ["a1"]


async def test_third_claim_does_not_collide_with_the_second():
    ref, conflicts = await _resolve_async([
        {"id": "a1", "ref": "decision/use-postgres", "body": "X", "source": "conv/9"},
        {"id": "a2", "ref": "decision/use-postgres--2", "body": "Y", "source": "conv/8"},
    ], src="conv/1", body="Z")
    assert ref == "decision/use-postgres--3"
    assert set(conflicts) == {"a1", "a2"}


async def test_lookup_failure_falls_back_to_todays_behaviour():
    from scripts import distill

    class _Boom:
        async def __aenter__(self):
            raise RuntimeError("relation artifact_links does not exist")

        async def __aexit__(self, *_a):
            return False

    with patch.object(distill, "service_session", lambda: _Boom()):
        ref, conflicts = await distill._resolve_decision_ref(
            "t", "decision/x", "conv/1", "B")
    assert ref == "decision/x"
    assert conflicts == []


# ---------------------------------------------------------------------------
# Link writing
# ---------------------------------------------------------------------------

async def test_create_link_refuses_a_self_link():
    """Cheap mistake for a batch writer to make; must fail loudly rather than
    as a constraint error buried in a rollback."""
    with pytest.raises(ValueError, match="self-link"):
        await create_link(_FakeSession([]), "t", "same-id", "same-id", "contradicts")


def test_create_link_is_idempotent():
    src = _read("lab_lib/links.py")
    assert "ON CONFLICT (src_artifact_id, dst_artifact_id, kind) DO NOTHING" in src


# ---------------------------------------------------------------------------
# Expansion — must only ever ADD
# ---------------------------------------------------------------------------

async def test_expansion_is_skipped_when_results_are_already_full():
    """The common case. Costs nothing on a normal query."""
    items = [{"artifact_id": str(i)} for i in range(8)]
    out = await expand_with_links(
        _FakeSession([]), "t", items, limit=8,
        visibility_sql="TRUE", viewer_member_id="")
    assert out == items


async def test_expansion_is_skipped_for_an_empty_result_set():
    out = await expand_with_links(
        _FakeSession([]), "t", [], limit=8,
        visibility_sql="TRUE", viewer_member_id="")
    assert out == []


async def test_expansion_appends_below_every_ranked_hit():
    base = [{"artifact_id": "a1", "score": 0.42}]
    out = await expand_with_links(
        _FakeSession([{"artifact_id": "a2", "ref": "spec.md", "type": "note",
                       "date": None, "slug": None, "origin": "raw",
                       "link_kind": "derived_from", "link_open_conflict": False,
                       "chunk_id": "c1", "seq": 0, "content": "..."}]),
        "t", base, limit=8, visibility_sql="TRUE", viewer_member_id="")
    assert len(out) == 2
    assert out[0]["artifact_id"] == "a1", "a ranked hit must never be displaced"
    assert out[1]["score"] == 0.0, "expansions must sort below every real hit"
    assert out[1]["via_link"] == "derived_from"


async def test_expansion_does_not_duplicate_an_artifact_already_present():
    base = [{"artifact_id": "a1", "score": 0.4}]
    out = await expand_with_links(
        _FakeSession([{"artifact_id": "a1", "ref": "x", "type": "note",
                       "date": None, "slug": None, "origin": "raw",
                       "link_kind": "supports", "link_open_conflict": False,
                       "chunk_id": "c", "seq": 0, "content": "."}]),
        "t", base, limit=8, visibility_sql="TRUE", viewer_member_id="")
    assert len(out) == 1


async def test_expansion_failure_returns_the_base_results():
    class _Boom:
        async def execute(self, *_a, **_k):
            raise RuntimeError("no such table")

    base = [{"artifact_id": "a1", "score": 0.4}]
    out = await expand_with_links(_Boom(), "t", base, limit=8,
                                  visibility_sql="TRUE", viewer_member_id="")
    assert out == base


def test_contradictions_are_traversable():
    """If a cited decision is disputed, the answer must be able to see the
    dispute — that is the entire point of keeping both claims."""
    assert "contradicts" in EXPANDABLE_KINDS
    # 'mentions' is the weakest edge and the noisiest to follow.
    assert "mentions" not in EXPANDABLE_KINDS


# ---------------------------------------------------------------------------
# Schema + wiring contracts
# ---------------------------------------------------------------------------

def test_migration_008_shape():
    sql = (REPO / "infra" / "migrations" / "008_artifact_links.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS artifact_links" in sql
    for kind in ("mentions", "derived_from", "supports", "contradicts", "supersedes"):
        assert f"'{kind}'" in sql
    assert "UNIQUE (src_artifact_id, dst_artifact_id, kind)" in sql
    assert "artifact_links_no_self CHECK (src_artifact_id <> dst_artifact_id)" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    # The hygiene job (#143) sweeps exactly this predicate.
    assert "WHERE kind = 'contradicts' AND resolved_at IS NULL" in sql
    assert "INSERT INTO schema_migrations(name) VALUES ('008_artifact_links')" in sql


def test_distill_records_conflicts_loudly():
    """An unresolved contradiction is a finding, not routine output."""
    src = _read("scripts/distill.py")
    assert "_resolve_decision_ref" in src
    assert "_record_conflicts" in src
    assert "needs review" in src


def test_search_paths_expand_and_read_paths_expose_links():
    src = _read("applications/sediment_platform/routers/library.py")
    assert src.count("await expand_with_links(") == 2, (
        "both search paths (offline + hybrid) must expand"
    )
    assert '@router.get("/links/{ref:path}")' in src
    assert '@router.post("/links/{link_id}/resolve")' in src


def test_link_routes_precede_the_catch_all_ref_route():
    src = _read("applications/sediment_platform/routers/library.py")
    catch_all = src.index('@router.get("/{ref:path}")')
    assert src.index('@router.get("/links/{ref:path}")') < catch_all
    assert src.index('@router.get("/revisions/{ref:path}")') < catch_all


def test_links_read_is_symmetric_and_visibility_scoped():
    """A disputed page is disputed whichever end you arrived from — and the
    artifact on the other end is subject to #140's boundary too."""
    src = _read("applications/sediment_platform/routers/library.py")
    assert "l.src_artifact_id = self.id OR l.dst_artifact_id = self.id" in src
    assert 'visibility_filter_sql("self")' in src
    assert 'visibility_filter_sql("other")' in src
