"""Unit tests for the Collection AI Agent `decide()` function."""
from __future__ import annotations

from datetime import datetime, timezone

from lab_lib.collection_agent import decide, list_default_rules, supported_source_kinds
from lab_lib.connectors.base import NormalizedEvent


def _ev(kind: str, path: str = "", channel: str = "", **payload) -> NormalizedEvent:
    return NormalizedEvent(
        source="github",
        kind=kind,
        external_id="ext-1",
        ts=datetime.now(timezone.utc),
        payload={"path": path, "channel": channel, **payload},
        resource_id="r1",
    )


# ---------------------------------------------------------------------------
# vault — the most common case
# ---------------------------------------------------------------------------

def test_vault_default_ingests_md():
    d = decide(_ev("file_revision", path="vault/wiki/notes.md"),
               {"source_kind": "vault"})
    assert d.ingest is True
    assert d.notify is False


def test_vault_skips_dot_raw():
    d = decide(_ev("file_revision", path="vault/.raw/uploads/x.md"),
               {"source_kind": "vault"})
    assert d.ingest is False
    assert d.notify is False
    assert "excluded" in d.matched_rule


def test_vault_skips_dot_obsidian():
    d = decide(_ev("file_revision", path="vault/.obsidian/workspace.md"),
               {"source_kind": "vault"})
    assert d.ingest is False


def test_vault_decision_path_triggers_notify():
    d = decide(_ev("file_revision", path="vault/wiki/decisions/adopt-x.md"),
               {"source_kind": "vault"})
    assert d.ingest is True
    assert d.notify is True
    assert d.notify_event_type == "new_decision"
    assert d.notify_template == "new_decision"


def test_vault_adr_filename_also_triggers_notify():
    d = decide(_ev("file_revision", path="vault/wiki/specs/adr-langfuse.md"),
               {"source_kind": "vault"})
    assert d.notify is True
    assert d.notify_event_type == "new_decision"


def test_vault_file_delete_no_action():
    d = decide(_ev("file_delete", path="vault/wiki/notes.md"),
               {"source_kind": "vault"})
    # Caller handles delete separately (hard-delete chunks); decide() opts out
    assert d.ingest is False
    assert d.notify is False


# ---------------------------------------------------------------------------
# product
# ---------------------------------------------------------------------------

def test_product_skips_random_code():
    d = decide(_ev("file_revision", path="src/foo/bar.py"),
               {"source_kind": "product"})
    assert d.ingest is False
    assert d.notify is False


def test_product_ingests_docs_md():
    d = decide(_ev("file_revision", path="docs/architecture.md"),
               {"source_kind": "product"})
    assert d.ingest is True


def test_product_ingests_root_canonical_md():
    for fname in ("SPEC.md", "DECISIONS.md", "README.md", "CHANGELOG.md"):
        d = decide(_ev("file_revision", path=fname),
                   {"source_kind": "product"})
        assert d.ingest is True, f"expected ingest for {fname}"


def test_product_release_notifies():
    d = decide(_ev("release", version="v1.0.0"),
               {"source_kind": "product"})
    assert d.ingest is False
    assert d.notify is True
    assert d.notify_event_type == "release_published"


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------

def test_harness_skill_md_notifies():
    d = decide(_ev("file_revision", path="skills/skill-creator/SKILL.md"),
               {"source_kind": "harness"})
    assert d.ingest is True
    assert d.notify is True
    assert d.notify_event_type == "skill_published"


def test_harness_docs_ingest_only():
    d = decide(_ev("file_revision", path="docs/MEMBER-GUIDE.ko.md"),
               {"source_kind": "harness"})
    assert d.ingest is True
    assert d.notify is False


def test_harness_skips_scripts():
    d = decide(_ev("file_revision", path="scripts/sync.sh"),
               {"source_kind": "harness"})
    assert d.ingest is False


# ---------------------------------------------------------------------------
# transcript
# ---------------------------------------------------------------------------

def test_transcript_default_ingest_no_notify():
    d = decide(_ev("message", channel="ai-에이전트"),
               {"source_kind": "transcript"})
    assert d.ingest is True
    assert d.notify is False
    assert d.distill_strategy == "chat_thread"


def test_transcript_meeting_notes_uses_meeting_strategy():
    d = decide(_ev("message", channel="meeting-notes"),
               {"source_kind": "transcript"})
    assert d.ingest is True
    assert d.distill_strategy == "meeting_transcript"


def test_transcript_noise_channel_skipped():
    d = decide(_ev("message", channel="공지사항"),
               {"source_kind": "transcript"})
    assert d.ingest is False
    assert d.notify is False


# ---------------------------------------------------------------------------
# artifacts
# ---------------------------------------------------------------------------

def test_artifacts_only_release_notifies():
    d_release = decide(_ev("release"), {"source_kind": "artifacts"})
    assert d_release.notify is True
    assert d_release.ingest is False

    d_other = decide(_ev("file_revision", path="binary.tar.gz"),
                     {"source_kind": "artifacts"})
    assert d_other.notify is False
    assert d_other.ingest is False


# ---------------------------------------------------------------------------
# tenant overrides
# ---------------------------------------------------------------------------

def test_tenant_override_can_add_extra_notify_channels():
    cfg = {
        "source_kind": "vault",
        "event_rules": [
            {
                "name": "broadcast-extra",
                "when": {"path_globs": ["**/decisions/**"]},
                "action": {"notify_channels": ["primary", "meeting-notes"]},
            }
        ],
    }
    d = decide(_ev("file_revision", path="vault/decisions/x.md"), cfg)
    assert d.notify is True
    assert "meeting-notes" in d.notify_channels


def test_tenant_override_can_suppress_default():
    cfg = {
        "source_kind": "vault",
        "event_rules": [
            {
                "name": "skip-noisy-folder",
                "when": {"path_globs": ["vault/noisy/**"]},
                "action": {"ingest": False, "notify": False},
            }
        ],
    }
    d_noisy = decide(_ev("file_revision", path="vault/noisy/junk.md"), cfg)
    assert d_noisy.ingest is False    # tenant override wins
    d_other = decide(_ev("file_revision", path="vault/wiki/x.md"), cfg)
    assert d_other.ingest is True     # unaffected


# ---------------------------------------------------------------------------
# config defaults / missing
# ---------------------------------------------------------------------------

def test_missing_config_defaults_to_vault():
    d = decide(_ev("file_revision", path="something.md"), None)
    # Default = vault → md gets ingested
    assert d.ingest is True


def test_unknown_source_kind_falls_back_to_vault():
    d = decide(_ev("file_revision", path="something.md"),
               {"source_kind": "unknown_thing"})
    assert d.ingest is True


def test_notify_picks_default_channel_if_none_specified():
    """When a default rule says `notify: true` but didn't pick channels, we
    fall back to the integration's `notify.default_channels` (or ['primary'])."""
    cfg = {
        "source_kind": "vault",
        "notify": {"default_channels": ["sediment"]},
    }
    d = decide(_ev("file_revision", path="vault/decisions/x.md"), cfg)
    assert d.notify is True
    assert d.notify_channels == ["sediment"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def test_supported_source_kinds_lists_all():
    kinds = supported_source_kinds()
    assert {"vault", "product", "harness", "transcript", "artifacts"}.issubset(kinds)


def test_list_default_rules_returns_copy():
    rules = list_default_rules("vault")
    assert len(rules) > 0
    # Mutating the copy must not affect the canonical list
    rules.append({"injected": True})
    assert "injected" not in [r.get("name") for r in list_default_rules("vault")]
