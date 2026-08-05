"""sediment#163 — Discord capture becomes tenant data, like GitHub already is.

The issue originally proposed a new `tenant_connector_state` table. Reading the
code before building showed that was wrong twice over: Discord capture already
resumes from a snowflake watermark (not the sliding time window the issue
claimed), and `github_repo_fetch._save_state` already stores connector state in
`integrations.config` via `jsonb_set`. The remaining gaps were narrower — the
Discord channel list is hardcoded in cron.yaml, the fetch runs for one tenant,
and its watermark is derived rather than stored.

So this adds no table and no migration. It makes Discord follow the pattern
GitHub established.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.discord_fetch import channels_from_config, stored_watermark

REPO = Path(__file__).resolve().parents[3]
SVC = REPO / "services" / "sediment"


def _read(rel: str) -> str:
    return (SVC / rel).read_text()


# ---------------------------------------------------------------------------
# Reading tenant config
# ---------------------------------------------------------------------------

def test_resources_become_channels():
    chans = channels_from_config({"resources": [
        {"id": "111", "name": "weekly"},
        {"id": "222", "name": "daily-research"},
    ]})
    assert chans == [{"id": "111", "name": "weekly"},
                     {"id": "222", "name": "daily-research"}]


def test_a_resource_without_an_id_is_dropped():
    """A channel we cannot address is not a channel."""
    assert channels_from_config({"resources": [{"name": "no-id"}]}) == []


def test_name_defaults_to_the_id():
    assert channels_from_config({"resources": [{"id": "111"}]}) == [
        {"id": "111", "name": "111"}]


@pytest.mark.parametrize("cfg", [{}, None, {"resources": None}, {"resources": []}])
def test_absent_config_yields_no_channels(cfg):
    assert channels_from_config(cfg) == []


# ---------------------------------------------------------------------------
# Reading the stored watermark
# ---------------------------------------------------------------------------

def test_stored_watermark_is_read_per_channel():
    cfg = {"state": {"watermarks": {"111": "999", "222": "888"}}}
    assert stored_watermark(cfg, "111") == "999"
    assert stored_watermark(cfg, "222") == "888"


@pytest.mark.parametrize("cfg", [{}, None, {"state": {}}, {"state": {"watermarks": {}}}])
def test_missing_watermark_is_none_not_an_error(cfg):
    """None is the signal to fall back to the derived watermark — it must not
    raise, or a tenant with no stored state would break the whole fetch."""
    assert stored_watermark(cfg, "111") is None


# ---------------------------------------------------------------------------
# Target selection — tenant data wins, yaml is a fallback
# ---------------------------------------------------------------------------

async def _targets(integrations, yaml_channels):
    from scripts import scheduler
    with patch("scripts.discord_fetch.load_discord_integrations",
               new=lambda *_a, **_k: _returns(integrations)):
        return await scheduler._discord_targets(yaml_channels)


async def _returns(v):
    return v


YAML = [{"id": "yaml-1", "name": "from-yaml"}]


async def test_db_config_wins_over_yaml():
    targets = await _targets(
        [{"id": "int-1", "tenant_id": "t1", "slug": "a",
          "config": {"resources": [{"id": "111", "name": "weekly"}]}}],
        YAML)
    assert [t["channel"]["id"] for t in targets] == ["111"]
    assert targets[0]["tenant_id"] == "t1"
    assert targets[0]["integration_id"] == "int-1"


async def test_every_tenant_is_fetched_not_just_the_default():
    """The single-tenant hardcode (`_default_tenant_id`) was the real gap —
    GitHub already iterated all tenants."""
    targets = await _targets([
        {"id": "i1", "tenant_id": "t1", "slug": "a",
         "config": {"resources": [{"id": "111"}]}},
        {"id": "i2", "tenant_id": "t2", "slug": "b",
         "config": {"resources": [{"id": "222"}, {"id": "333"}]}},
    ], YAML)
    assert {t["tenant_id"] for t in targets} == {"t1", "t2"}
    assert len(targets) == 3


async def test_no_db_config_falls_back_to_yaml():
    """Removing the yaml list outright would silently stop capture on the
    current deployment, which has no discord integration row. A config change
    that turns off ingestion is worse than a duplicated list."""
    targets = await _targets([], YAML)
    assert [t["channel"]["id"] for t in targets] == ["yaml-1"]
    assert targets[0]["tenant_id"] is None
    assert targets[0]["integration_id"] is None, (
        "the yaml path has no integrations row, so there is nowhere to store a "
        "watermark — it must stay on the derived one"
    )


async def test_unreadable_integrations_falls_back_rather_than_dying():
    from scripts import scheduler

    async def _boom(*_a, **_k):
        raise RuntimeError("DB down")

    with patch("scripts.discord_fetch.load_discord_integrations", new=_boom):
        targets = await scheduler._discord_targets(YAML)
    assert [t["channel"]["id"] for t in targets] == ["yaml-1"]


# ---------------------------------------------------------------------------
# Source contracts
# ---------------------------------------------------------------------------

def test_watermark_is_stored_the_same_way_github_stores_its_state():
    """One mechanism, one place. Two connectors persisting state differently is
    how they drift."""
    dis = _read("scripts/discord_fetch.py")
    gh = _read("scripts/github_repo_fetch.py")
    assert "jsonb_set" in dis and "jsonb_set" in gh
    assert "UPDATE integrations" in dis and "UPDATE integrations" in gh
    assert "last_sync_at = now()" in dis


def test_no_new_table_was_introduced():
    """The issue proposed tenant_connector_state; the code already had the
    mechanism. Adding the table would have duplicated it — and added a seventh
    unverified migration."""
    dis = _read("scripts/discord_fetch.py")
    assert "CREATE TABLE" not in dis
    migrations = sorted((REPO / "infra" / "migrations").glob("*.sql"))
    assert not any("connector" in m.name for m in migrations)


def test_stored_watermark_is_preferred_but_derived_remains_the_fallback():
    """Existing deployments have events but no stored watermark. Without the
    fallback the first run after this change would re-fetch each channel from
    the beginning."""
    src = _read("scripts/discord_fetch.py")
    i_stored = src.index("after_external_id = stored_watermark(")
    i_derived = src.index("after_external_id = await _get_watermark(")
    assert i_stored < i_derived


def test_watermark_advances_only_on_persisted_messages():
    """Advancing on fetch would skip past anything that failed to insert, and
    the gap would never be retried."""
    src = _read("scripts/discord_fetch.py")
    assert "events[-1].external_id if events and inserted else None" in src


def test_failing_to_store_the_watermark_is_not_fatal():
    """The derived fallback still finds those messages next run — losing the
    store costs a wider re-scan, not data."""
    src = _read("scripts/discord_fetch.py")
    assert "discord.watermark.save_failed" in src
