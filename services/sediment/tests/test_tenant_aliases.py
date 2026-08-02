"""sediment#139 — retrieval vocabulary is tenant data, not Python constants.

The boost maps used to hardcode HypeProof proper nouns ("동아일보", "칼럼",
"시뮬라크라") inside a multi-tenant retrieval path. Two things must hold now:

1. the matching BEHAVIOUR is unchanged for a tenant whose vocabulary is loaded
   (including the "칼럼이나" token-vs-substring bug that token matching fixed);
2. a tenant with no vocabulary gets NO boosts — not another tenant's.

The last group asserts the proper nouns are actually gone from the source. That
is the regression that matters: reintroducing one is a two-line edit and
nothing else would catch it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lab_lib.aliases import (
    EMPTY_INDEX,
    AliasIndex,
    build_index,
    demote_case_sql,
    invalidate_cache,
    load_alias_index,
)

REPO = Path(__file__).resolve().parents[3]
SVC = REPO / "services" / "sediment"

# The vocabulary migration 006 seeds for the default tenant — i.e. exactly what
# used to be hardcoded. Tests below use it to prove behaviour is preserved.
SEED_ROWS = [
    ("칼럼", "type", "column"),
    ("column", "type", "column"),
    ("리서치", "type", "research"),
    ("research", "type", "research"),
    ("소설", "type", "novel"),
    ("동아일보", "ref_prefix", "donga"),
    ("동아", "ref_prefix", "donga"),
    ("아카데미", "ref_prefix", "ai-architect-academy"),
    ("시뮬라크라", "ref_prefix", "simulacra"),
    ("products/sediment/SPEC", "demote_ref_prefix", "products/sediment/SPEC"),
    ("products/sediment/README", "demote_ref_prefix", "products/sediment/README"),
]


@pytest.fixture
def seeded() -> AliasIndex:
    return build_index(SEED_ROWS)


def _read(rel: str) -> str:
    return (SVC / rel).read_text()


# ---------------------------------------------------------------------------
# Behaviour preserved for a tenant that HAS the vocabulary
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query,expected", [
    ("AI 보안 칼럼 하나 써줘", "column"),
    ("latest research on agents", "research"),
    ("소설 초고 보여줘", "novel"),
    ("아무 관련 없는 질문", None),
])
def test_type_detection_matches_the_old_map(seeded, query, expected):
    assert seeded.detect_type(query) == expected


def test_type_detection_is_token_based_not_substring(seeded):
    """"칼럼이나" means "column-or" — a query using it is not asking for
    columns. Substring matching used to fire the 3x column boost here."""
    assert seeded.detect_type("칼럼이나 제안 아무거나") is None
    assert seeded.detect_type("칼럼 제안") == "column"


@pytest.mark.parametrize("query,expected", [
    ("동아일보 관련 칼럼이나 제안", "donga"),
    ("아카데미 커리큘럼", "ai-architect-academy"),
    ("시뮬라크라 진행상황", "simulacra"),
    ("전혀 다른 질문", ""),
])
def test_ref_prefix_detection_matches_the_old_map(seeded, query, expected):
    assert seeded.detect_ref_prefix(query) == expected


def test_confidence_order_breaks_ties_deterministically():
    """Rows arrive ordered by (confidence DESC, alias); dict insertion order
    then decides which target a colliding alias resolves to."""
    idx = build_index([("x", "type", "column"), ("x", "type", "novel")])
    assert idx.detect_type("x") == "column"


# ---------------------------------------------------------------------------
# A tenant with no vocabulary gets nothing — not someone else's
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query", [
    "동아일보 관련 칼럼이나 제안",
    "시뮬라크라 진행상황",
    "AI 보안 칼럼",
])
def test_unconfigured_tenant_gets_no_boosts(query):
    assert EMPTY_INDEX.detect_type(query) is None
    assert EMPTY_INDEX.detect_ref_prefix(query) == ""


def test_demote_sql_collapses_to_a_constant_when_unconfigured():
    sql, params = demote_case_sql(EMPTY_INDEX)
    assert sql == "1.0"
    assert params == {}


def test_demote_sql_binds_one_param_per_prefix(seeded):
    sql, params = demote_case_sql(seeded, "a")
    assert params == {
        "demote_0": "products/sediment/SPEC%",
        "demote_1": "products/sediment/README%",
    }
    assert sql.count("a.ref LIKE") == 2
    assert "THEN 0.8 ELSE 1.0" in sql


def test_entity_rows_are_ignored_by_retrieval_for_now():
    """'entity' is reserved for the pages of #140/#141; it must not silently
    start acting as a type or ref boost."""
    idx = build_index([("라이언", "entity", "Ryan")])
    assert idx.detect_type("라이언") is None
    assert idx.detect_ref_prefix("라이언") == ""


# ---------------------------------------------------------------------------
# Loading: cached, and never fatal
# ---------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    """Counts executes so the cache can be observed."""

    def __init__(self, rows=None, raises=None):
        self.rows = rows or []
        self.raises = raises
        self.calls = 0

    async def execute(self, *_args, **_kwargs):
        self.calls += 1
        if self.raises:
            raise self.raises
        return _FakeResult(self.rows)


async def test_load_caches_per_tenant():
    invalidate_cache()
    s = _FakeSession(rows=SEED_ROWS)
    tid = "11111111-1111-1111-1111-111111111111"
    first = await load_alias_index(s, tid)
    second = await load_alias_index(s, tid)
    assert s.calls == 1, "second load should come from cache"
    assert first is second
    assert first.detect_type("칼럼 제안") == "column"
    invalidate_cache()


async def test_load_degrades_to_empty_when_the_table_is_missing():
    """A cluster that has not run migration 006 must still return search
    results — unranked beats none."""
    invalidate_cache()
    s = _FakeSession(raises=RuntimeError('relation "tenant_aliases" does not exist'))
    idx = await load_alias_index(s, "22222222-2222-2222-2222-222222222222")
    assert idx == EMPTY_INDEX
    invalidate_cache()


async def test_invalidate_is_scoped_to_one_tenant():
    invalidate_cache()
    a, b = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    sa, sb = _FakeSession(rows=SEED_ROWS), _FakeSession(rows=SEED_ROWS)
    await load_alias_index(sa, a)
    await load_alias_index(sb, b)
    invalidate_cache(a)
    await load_alias_index(sa, a)
    await load_alias_index(sb, b)
    assert sa.calls == 2, "invalidated tenant should reload"
    assert sb.calls == 1, "other tenant's cache should survive"
    invalidate_cache()


# ---------------------------------------------------------------------------
# The proper nouns are actually gone
# ---------------------------------------------------------------------------

WORKSPACE_PROPER_NOUNS = [
    "동아일보", "아카데미", "시뮬라크라", "hypeproof-roadmap",
    "ai-architect-academy", "products/sediment/SPEC",
]


@pytest.mark.parametrize("rel", [
    "lab_lib/search_utils.py",
    "applications/sediment_platform/routers/library.py",
    "applications/sediment_langgraph/graphs/lab_curator_graph.py",
])
def test_retrieval_source_has_no_workspace_proper_nouns(rel):
    src = _read(rel)
    found = [n for n in WORKSPACE_PROPER_NOUNS if n in src]
    assert not found, (
        f"{rel} still hardcodes tenant vocabulary {found}; "
        "put it in tenant_aliases instead"
    )


def test_library_imports_shared_helpers_instead_of_copying_them():
    """WO-7 extracted these to search_utils and library.py kept verbatim
    copies anyway. Pin the import so the copies cannot come back."""
    src = _read("applications/sediment_platform/routers/library.py")
    assert "from lab_lib.search_utils import" in src
    for dup in ("_STOP_WORDS = frozenset", "_KO_PARTICLE_SUFFIXES: tuple",
                "def _build_ts_or_query", "def _prefer_bm25_first",
                "def _slug_regex"):
        assert dup not in src, f"library.py re-duplicates {dup!r}"


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def test_migration_006_creates_the_table_with_rls_and_seeds():
    sql = (REPO / "infra" / "migrations" / "006_tenant_retrieval_aliases.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS tenant_aliases" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "tenant_id = current_tenant_id()" in sql
    # Seeding is what keeps the default tenant's recall from silently regressing.
    for alias, _kind, _target in SEED_ROWS:
        assert f"'{alias}'" in sql, f"migration does not seed {alias!r}"
    assert "WHERE t.slug = 'hypeproof-lab'" in sql, (
        "seeds must be scoped to the default tenant, not applied to every tenant"
    )
    assert "INSERT INTO schema_migrations(name) VALUES ('006_tenant_retrieval_aliases')" in sql
