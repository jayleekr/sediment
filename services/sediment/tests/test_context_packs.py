"""sediment#142 — context packs must stay cheap and stay scoped.

A wiki's hot.md exists so a session picks up recent context without crawling.
Sediment had no equivalent: a new session either ran a vector search — which
answers a question it does not yet know to ask — or started blind.

Two properties are constraints rather than features, and both are tested here:
a pack must be cheap enough to read unconditionally, and it must never show a
member something they could not open directly.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lab_lib.context_packs import (
    HOT_TOKEN_BUDGET,
    INDEX_TOKEN_BUDGET,
    _truncate_to_budget,
    build_hot_pack,
    build_index_pack,
    estimate_tokens,
    parse_scope,
    rebuild_packs,
)

REPO = Path(__file__).resolve().parents[3]
SVC = REPO / "services" / "sediment"


def _read(rel: str) -> str:
    return (SVC / rel).read_text()


# ---------------------------------------------------------------------------
# Scope parsing — the access boundary starts here
# ---------------------------------------------------------------------------

def test_parse_scope_recognises_the_three_shapes():
    assert parse_scope("tenant") == ("tenant", None)
    assert parse_scope("member:abc-123") == ("member", "abc-123")
    assert parse_scope("domain:retrieval") == ("domain", "retrieval")


@pytest.mark.parametrize("bad", ["member", "member:", "", "everyone", "user:1"])
def test_parse_scope_rejects_anything_else(bad):
    """A malformed scope must not fall through to a default — the scope is what
    decides whose visibility the pack is built with."""
    with pytest.raises(ValueError):
        parse_scope(bad)


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------

def test_truncation_keeps_whole_lines():
    """Mid-sentence truncation hides the fact that the budget was blown."""
    body = "\n".join(f"- line {i} with some padding text here" for i in range(500))
    out = _truncate_to_budget(body, 50)
    assert estimate_tokens(out) <= 50 + estimate_tokens(
        "\n\n_(truncated to fit the context-pack budget)_\n")
    assert "truncated" in out
    # No partial line survives.
    for line in out.split("\n"):
        if line.startswith("- line"):
            assert line.endswith("here"), f"partial line kept: {line!r}"


def test_short_bodies_are_untouched():
    body = "# Recent context\n\n- one thing\n"
    assert _truncate_to_budget(body, HOT_TOKEN_BUDGET) == body


def test_hot_budget_is_tighter_than_index():
    """hot is read on every session start; index only when hot was not enough."""
    assert HOT_TOKEN_BUDGET < INDEX_TOKEN_BUDGET


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------

class _Row:
    def __init__(self, d):
        self._mapping = d


class _Result:
    def __init__(self, rows):
        self._rows = [_Row(r) for r in rows]

    def __iter__(self):
        return iter(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _Session:
    """Returns queued result sets in order, recording bound params."""

    def __init__(self, *result_sets):
        self.queue = list(result_sets)
        self.params: list[dict] = []
        self.sql: list[str] = []

    async def execute(self, stmt, params=None):
        self.sql.append(str(stmt))
        self.params.append(params or {})
        return _Result(self.queue.pop(0) if self.queue else [])


async def test_hot_pack_leads_with_open_contradictions():
    """An unresolved conflict is the single most useful thing a session can
    know before it starts answering — and the thing that used to be invisible."""
    s = _Session(
        [{"ref": "note/a", "type": "note", "origin": "raw", "updated_at": None}],
        [{"ref": "decision/x", "topic": "Use Postgres", "updated_at": None}],
        [{"src_ref": "decision/x--2", "dst_ref": "decision/x", "created_at": None}],
    )
    pack = await build_hot_pack(s, "t", "tenant")
    body = pack.body
    assert body.index("Open contradictions") < body.index("Latest decisions")
    assert "`decision/x--2` vs `decision/x`" in body
    assert pack.sources["open_contradictions"] == 1
    assert pack.token_estimate <= HOT_TOKEN_BUDGET


async def test_hot_pack_marks_synthesized_pages():
    s = _Session(
        [{"ref": "decision/x", "type": "decision", "origin": "derived",
          "updated_at": None},
         {"ref": "spec.md", "type": "note", "origin": "raw", "updated_at": None}],
        [], [],
    )
    pack = await build_hot_pack(s, "t", "tenant")
    assert "`decision/x` — decision *(synthesized)*" in pack.body
    assert "`spec.md` — note\n" in pack.body


async def test_empty_scope_says_so_rather_than_returning_nothing():
    """"nothing here" and "the pack failed to build" must not look the same to
    a reading session."""
    s = _Session([], [], [])
    pack = await build_hot_pack(s, "t", "tenant")
    assert "No artifacts visible in this scope yet" in pack.body


async def test_member_pack_is_built_with_that_members_visibility():
    """The viewer comes from the SCOPE, not from whoever triggered the rebuild.
    A pack is stored and re-read later by the member it belongs to; building it
    with the trigger's permissions would leak across members on the next read.
    """
    s = _Session([], [], [])
    await build_hot_pack(s, "t", "member:MEMBER-42")
    assert all(p["viewer_member_id"] == "MEMBER-42" for p in s.params)


async def test_tenant_pack_is_built_with_no_viewer():
    """No viewer → tenant-visible rows only. Fail-closed: the shared pack must
    not carry anybody's restricted pages."""
    s = _Session([], [], [])
    await build_hot_pack(s, "t", "tenant")
    assert all(p["viewer_member_id"] == "" for p in s.params)


async def test_index_pack_separates_sources_from_synthesized():
    s = _Session([
        {"type": "note", "origin": "raw", "n": 12, "latest": None},
        {"type": "decision", "origin": "derived", "n": 3, "latest": None},
    ])
    pack = await build_index_pack(s, "t", "tenant")
    assert "## Sources" in pack.body and "- note: 12" in pack.body
    assert "## Synthesized" in pack.body and "- decision: 3" in pack.body


async def test_rebuild_never_raises():
    """Pack generation runs off the back of ingestion; a failure here must not
    fail the ingest that triggered it."""
    class _Boom:
        async def execute(self, *_a, **_k):
            raise RuntimeError("no context_packs table")

    assert await rebuild_packs(_Boom(), "t", "tenant") == []


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

def test_migration_009_shape():
    sql = (REPO / "infra" / "migrations" / "009_context_packs.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS context_packs" in sql
    assert "PRIMARY KEY (tenant_id, scope_key, kind)" in sql
    assert "CHECK (kind IN ('hot','index'))" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "INSERT INTO schema_migrations(name) VALUES ('009_context_packs')" in sql


def test_api_refuses_to_hand_over_another_members_pack():
    """A member pack is built through that member's visibility; serving it to
    anyone else would launder the boundary #140 established."""
    src = _read("applications/sediment_platform/routers/vault.py")
    assert 'if scope_kind == "member" and value != identity.member_id' in src
    assert "status_code=403" in src


def test_mcp_exposes_both_packs():
    src = _read("lab_platform/mcp_servers/workspace_mcp.py")
    assert "async def sediment_hot(" in src
    assert "async def sediment_index(" in src


def test_ingest_refreshes_the_tenant_pack():
    """A hot pack that lags the vault is worse than none — a session reads it
    INSTEAD of looking."""
    src = _read("applications/vault_ingester/main.py")
    assert 'await rebuild_packs(s, tid, "tenant")' in src
