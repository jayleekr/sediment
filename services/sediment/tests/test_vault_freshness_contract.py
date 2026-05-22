"""Source-level tests for vault freshness telemetry.

These avoid DB requirements but still lock the important reliability contract:
the endpoint must look at each pipeline axis, and github cron sync must emit
the same freshness breadcrumb family as the webhook path.
"""
from __future__ import annotations

import inspect


pytestmark = []


def test_vault_freshness_reads_all_pipeline_axes():
    import applications.sediment_platform.routers.vault as vault

    src = inspect.getsource(vault.freshness)
    for token in [
        "vault.ingest",
        "vault.sync",
        "source = 'github'",
        "source = 'discord'",
        "max(a.updated_at)",
        "max(c.created_at)",
        "max(d.created_at)",
        "a.type = 'decision'",
    ]:
        assert token in src


def test_vault_freshness_has_explicit_tenant_filters():
    import applications.sediment_platform.routers.vault as vault

    src = inspect.getsource(vault.freshness)
    assert src.count("current_tenant_id()") >= 8


def test_github_repo_fetch_records_vault_sync_event():
    import scripts.github_repo_fetch as gh

    src = inspect.getsource(gh)
    assert "kind='vault.sync'" in src or "'vault.sync'" in src
    assert "_record_vault_sync" in src
    assert "files_ingested" in src
    assert "events_inserted" in src
