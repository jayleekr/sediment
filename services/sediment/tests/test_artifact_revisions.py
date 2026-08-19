"""sediment#138 — a replaced body must never vanish without a trace.

distill derives a ref from the decision's topic slug, and the ingester upserts
on (tenant_id, ref) with `body = EXCLUDED.body`. One author re-deciding is an
update; two authors deciding differently about the same topic is not, and the
second one used to erase the first — text, source and rationale — with nothing
recording that it had existed.

Source-level contract tests (no DB): they pin the archive-before-overwrite
shape, the conditions under which history and `rev` advance, and the read side.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SVC = REPO / "services" / "sediment"


def _read(rel: str) -> str:
    return (SVC / rel).read_text()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_migration_007_creates_append_only_history_with_rls():
    sql = (REPO / "infra" / "migrations" / "007_artifact_revisions.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS artifact_revisions" in sql
    assert "ADD COLUMN IF NOT EXISTS rev INT NOT NULL DEFAULT 1" in sql
    # One row per (artifact, rev): the archive cannot be written twice for the
    # same revision, which is what makes retries idempotent.
    assert "UNIQUE (artifact_id, rev)" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "tenant_id = current_tenant_id()" in sql
    # Attribution is the point — an anonymous history cannot explain a collision.
    for col in ("author_id", "source_ref", "replaced_at", "frontmatter"):
        assert col in sql, f"artifact_revisions is missing {col}"
    assert "INSERT INTO schema_migrations(name) VALUES ('007_artifact_revisions')" in sql


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------

def test_ingester_archives_the_previous_body_before_overwriting():
    src = _read("applications/vault_ingester/main.py")
    assert "WITH prev AS (" in src, "no pre-update snapshot of the existing row"
    assert "INSERT INTO artifact_revisions" in src
    # The archive must read the OLD row, not the incoming one.
    assert "SELECT prev.tenant_id, prev.id, prev.rev, prev.body" in src


def test_only_real_body_changes_are_archived_and_bump_rev():
    """The GitHub webhook re-ingests unchanged files routinely. Archiving those
    would bury the changes that matter under identical revisions."""
    src = _read("applications/vault_ingester/main.py")
    assert "WHERE prev.body IS DISTINCT FROM CAST(:body AS text)" in src
    assert "WHEN artifacts.body IS DISTINCT FROM EXCLUDED.body" in src
    assert "THEN artifacts.rev + 1 ELSE artifacts.rev" in src


def test_archive_insert_is_idempotent_on_retry():
    src = _read("applications/vault_ingester/main.py")
    assert "ON CONFLICT (artifact_id, rev) DO NOTHING" in src


def test_ingester_detects_lost_updates_when_a_caller_opts_in():
    src = _read("applications/vault_ingester/main.py")
    assert "expected_rev: Optional[int] = None" in src, (
        "default must stay last-writer-wins so existing callers are unaffected"
    )
    assert "status_code=409" in src
    assert "rev conflict on" in src


def test_response_returns_the_post_write_rev():
    """A caller cannot use optimistic concurrency without being told the rev
    its own write produced."""
    src = _read("applications/vault_ingester/main.py")
    assert "RETURNING id, rev" in src
    assert "rev=artifact_rev" in src


def test_distill_attributes_the_superseded_revision():
    src = _read("scripts/distill.py")
    assert '"source_ref": f"distill:{source_ref}" if source_ref else None' in src
    assert "source_ref=s[\"src\"]" in src, (
        "the conversation / event batch behind the replaced text must be recorded"
    )


# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------

def test_history_endpoint_exists_and_is_visibility_scoped():
    src = _read("applications/sediment_platform/routers/library.py")
    assert '@router.get("/revisions/{ref:path}")' in src
    assert "FROM artifact_revisions rv" in src
    # sediment#140's boundary applies to history too — a restricted artifact's
    # past bodies are no less restricted.
    assert "visibility_filter_sql" in src


def test_history_route_is_declared_before_the_catch_all_ref_route():
    """`/{ref:path}` would otherwise swallow "revisions/...". Routes match in
    declaration order, so this ordering is load-bearing, not cosmetic."""
    src = _read("applications/sediment_platform/routers/library.py")
    assert src.index('@router.get("/revisions/{ref:path}")') < src.index('@router.get("/{ref:path}")')
