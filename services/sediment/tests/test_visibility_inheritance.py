"""sediment#140 — the visibility rule that must exist before pages accumulate.

RLS enforces the tenant boundary and nothing else. Once Sediment synthesizes
pages from multiple sources, synthesis becomes a disclosure path: a page
summarizing five documents is readable by anyone who can read the page,
regardless of who could read the five.

The rule — a derived page inherits the MOST RESTRICTIVE visibility among its
sources — is only useful if it cannot be quietly bypassed. These tests pin the
rule itself, the fail-closed defaults, and the fact that every read path binds
the predicate. The last group is what stops a new retrieval path from shipping
without one.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lab_lib.visibility import (
    DEFAULT_VISIBILITY,
    VISIBILITY_LADDER,
    UnknownVisibility,
    inherit_visibility,
    is_more_restrictive,
    rank,
    sql_rank_expr,
    viewer_member_id,
    visibility_filter_sql,
)

REPO = Path(__file__).resolve().parents[3]
SVC = REPO / "services" / "sediment"


def _read(rel: str) -> str:
    return (SVC / rel).read_text()


# ---------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------

def test_ladder_is_ordered_most_restrictive_first():
    assert VISIBILITY_LADDER[0] == "private"
    assert VISIBILITY_LADDER[-1] == "tenant"
    assert DEFAULT_VISIBILITY == "tenant"
    assert rank("private") < rank("tenant")
    assert is_more_restrictive("private", "tenant")
    assert not is_more_restrictive("tenant", "private")


def test_unknown_visibility_raises_rather_than_defaulting():
    """A typo must not silently become tenant-wide — that is the whole failure
    mode this module exists to prevent."""
    with pytest.raises(UnknownVisibility):
        rank("publik")
    with pytest.raises(UnknownVisibility):
        inherit_visibility(["tenant", "publik"])


# ---------------------------------------------------------------------------
# Inheritance
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sources,expected", [
    (["tenant", "tenant"], "tenant"),
    (["tenant", "private"], "private"),      # one restricted source restricts the page
    (["private", "tenant"], "private"),      # order must not matter
    (["private"], "private"),
    ([None, "tenant"], "tenant"),            # pre-migration NULL → default
    ([None, "private"], "private"),
])
def test_inherit_takes_the_most_restrictive_source(sources, expected):
    assert inherit_visibility(sources) == expected


def test_empty_source_set_is_default_not_most_restrictive():
    """A page derived from nothing is unsourced, not secret. Returning
    'private' here would make every sourceless derived page invisible and hide
    the actual problem (that it has no sources)."""
    assert inherit_visibility([]) == DEFAULT_VISIBILITY


def test_inheritance_is_monotonic_over_added_sources():
    """Adding a source can only ever narrow, never widen — the property that
    makes the rule safe to apply incrementally."""
    acc = inherit_visibility(["tenant"])
    for nxt in ("tenant", "private", "tenant"):
        new = inherit_visibility([acc, nxt])
        assert rank(new) <= rank(acc)
        acc = new
    assert acc == "private"


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------

def test_filter_sql_allows_tenant_rows_and_own_restricted_rows():
    sql = visibility_filter_sql("a")
    assert "a.visibility = 'tenant'" in sql
    assert "a.author_id" in sql
    # NULLIF so an empty bind becomes SQL NULL rather than a failed uuid cast.
    assert "NULLIF(:viewer_member_id, '')" in sql


def test_sql_rank_expr_is_generated_from_the_ladder():
    """SQL and Python restrictiveness must not drift when a level is added."""
    expr = sql_rank_expr("x.visibility")
    for i, level in enumerate(VISIBILITY_LADDER):
        assert f"WHEN '{level}' THEN {i}" in expr
    # Unknown/NULL → 0 = most restrictive (fail-closed).
    assert "ELSE 0 END" in expr


def test_viewer_member_id_blanks_service_identities():
    class _Svc:
        member_id = "service:cron"
        is_service = True

    class _Human:
        member_id = "11111111-1111-1111-1111-111111111111"
        is_service = False

    assert viewer_member_id(_Svc()) == ""
    assert viewer_member_id(_Human()) == _Human.member_id


# ---------------------------------------------------------------------------
# Wiring — every read path must bind the predicate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rel", [
    "applications/sediment_platform/routers/library.py",
    "applications/sediment_langgraph/graphs/lab_curator_graph.py",
    "lab_platform/mcp_servers/workspace_mcp.py",
])
def test_read_paths_apply_the_visibility_predicate(rel):
    src = _read(rel)
    assert "visibility_filter_sql" in src, f"{rel} does not apply the visibility filter"
    assert "viewer_member_id" in src, f"{rel} does not bind a viewer"


def test_library_binds_a_viewer_everywhere_it_filters():
    """The predicate references :viewer_member_id, so every query that includes
    it must also bind it — an unbound parameter is a 500, not a silent pass."""
    src = _read("applications/sediment_platform/routers/library.py")
    assert src.count("visibility_filter_sql(") == src.count('"viewer_member_id"')


def test_ingester_marks_derived_and_never_widens_on_reingest():
    src = _read("applications/vault_ingester/main.py")
    assert "origin: str = \"raw\"" in src, "default must keep existing callers raw"
    assert "visibility: str = DEFAULT_VISIBILITY" in src
    # Re-ingest must not widen an already-restricted page.
    assert "sql_rank_expr(\"EXCLUDED.visibility\")" in src
    assert "sql_rank_expr(\"artifacts.visibility\")" in src


def test_distill_writes_derived_pages_through_the_inheritance_rule():
    src = _read("scripts/distill.py")
    assert '"origin": "derived"' in src
    assert "inherit_visibility(_source_visibilities(s))" in src, (
        "distill must go through the inheritance rule, not hardcode a visibility"
    )


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def test_migration_005_matches_the_python_ladder():
    sql = (REPO / "infra" / "migrations" / "005_derived_knowledge_layer.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS origin" in sql
    assert "ADD COLUMN IF NOT EXISTS visibility" in sql
    for level in VISIBILITY_LADDER:
        assert f"'{level}'" in sql, f"CHECK constraint is missing ladder level {level}"
    # Derived page types must be accepted by artifacts_type_check.
    for t in ("entity", "concept", "topic", "question", "comparison"):
        assert f"'{t}'" in sql
    assert "INSERT INTO schema_migrations(name) VALUES ('005_derived_knowledge_layer')" in sql


def test_migration_005_has_a_paired_rollback():
    """README mandates one for any migration that replaces a constraint."""
    assert (REPO / "infra" / "migrations" / "005_derived_knowledge_layer.rollback.sql").exists()


def test_migration_runner_ignores_rollback_files():
    """`005_x.rollback.sql` matches the plain NNN_*.sql shape. Without an
    explicit exclusion the runner would apply the rollback immediately after
    the migration it undoes."""
    from scripts.apply_migrations import NAME_RE, discover

    assert NAME_RE.match("005_derived_knowledge_layer.sql")
    assert not NAME_RE.match("005_derived_knowledge_layer.rollback.sql")
    assert not any(p.name.endswith(".rollback.sql") for p in discover())
