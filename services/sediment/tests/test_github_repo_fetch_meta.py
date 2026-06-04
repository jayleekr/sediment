"""Tests for self-describing GitHub ingest source metadata."""
from __future__ import annotations

from scripts.github_repo_fetch import _render_ingest_sources_doc


def test_render_ingest_sources_doc_lists_repos_paths_and_state():
    body = _render_ingest_sources_doc({
        "slug": "kids-edu",
        "tenant_id": "tenant-id",
        "config": {
            "repos": ["JinyongShin/hypeproof_kids_edu"],
            "path_prefixes": ["kids_edu_vault/wiki/", "meeting_notes/"],
            "path_excludes": [".raw/", ".obsidian/"],
            "extensions": [".md"],
            "branch": "main",
            "schedule": "0 0-13 * * *",
            "source_kind": "vault",
            "state": {
                "head_sha": "abc123",
                "last_sync_at": "2026-06-04T06:00:00Z",
            },
        },
    })

    assert "slug: ingest-sources" in body
    assert "# Ingest sources for tenant: kids-edu" in body
    assert "`JinyongShin/hypeproof_kids_edu`" in body
    assert "`kids_edu_vault/wiki/`" in body
    assert "`meeting_notes/`" in body
    assert "`abc123`" in body
    assert "`2026-06-04T06:00:00Z`" in body


def test_render_ingest_sources_doc_supports_legacy_single_repo_config():
    body = _render_ingest_sources_doc({
        "slug": "hypeproof-lab",
        "tenant_id": "tenant-id",
        "config": {
            "repo": "jayleekr/hypeprooflab",
            "include_globs": ["research/**/*.md"],
        },
    })

    assert "`jayleekr/hypeprooflab`" in body
    assert "`research/**/*.md`" in body
    assert "`repository default`" in body
