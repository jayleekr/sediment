"""Unit tests for GitHubRepoConnector — httpx MockTransport, no network."""
from __future__ import annotations

import base64
import json

import httpx
import pytest

from lab_lib.connectors.github_repo import (
    GitHubRepoConnector,
    _matches_filter,
)


# ---------------------------------------------------------------------------
# Filter unit tests (no network)
# ---------------------------------------------------------------------------

def test_filter_extension():
    assert _matches_filter("a/b.md", extensions=(".md",), path_prefixes=(), path_excludes=())
    assert not _matches_filter("a/b.txt", extensions=(".md",), path_prefixes=(), path_excludes=())


def test_filter_prefix():
    assert _matches_filter(
        "vault/wiki/x.md", extensions=(".md",),
        path_prefixes=("vault/",), path_excludes=(),
    )
    assert not _matches_filter(
        "other/x.md", extensions=(".md",),
        path_prefixes=("vault/",), path_excludes=(),
    )


def test_filter_excludes_segment():
    assert not _matches_filter(
        "vault/.raw/x.md", extensions=(".md",),
        path_prefixes=("vault/",), path_excludes=(".raw/",),
    )


# ---------------------------------------------------------------------------
# MockTransport — list_resources, initial sync, incremental
# ---------------------------------------------------------------------------

def _mock_repo_response() -> dict:
    return {"default_branch": "main"}


def _mock_head_commit(sha: str) -> dict:
    return {"sha": sha, "commit": {"author": {"date": "2026-05-21T12:00:00Z"}}}


def _mock_tree(sha: str, files: list[tuple[str, str]]) -> dict:
    """files = [(path, blob_sha), ...] — only blob entries."""
    return {
        "sha": sha,
        "truncated": False,
        "tree": [
            {"type": "blob", "path": p, "sha": b, "mode": "100644"}
            for p, b in files
        ],
    }


def _mock_contents(path: str, sha: str, content: str) -> dict:
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    return {
        "path": path,
        "sha": sha,
        "size": len(content.encode("utf-8")),
        "encoding": "base64",
        "content": encoded,
    }


def _mock_compare(commits: list[dict]) -> dict:
    return {"commits": commits, "behind_by": 0, "ahead_by": len(commits)}


def _mock_commit_detail(sha: str, files: list[dict], date: str = "2026-05-21T12:00:00Z") -> dict:
    return {
        "sha": sha,
        "commit": {
            "author": {"name": "Test", "date": date},
            "message": "test commit",
        },
        "files": files,
    }


def _make_handler(routes: dict):
    """routes: {(method, path): response_dict or callable(request)->Response}."""
    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        if key not in routes:
            return httpx.Response(404, json={"message": "not found in mock", "path": request.url.path})
        resp = routes[key]
        if callable(resp):
            return resp(request)
        return httpx.Response(200, json=resp)
    return handler


async def _make_connector(transport: httpx.MockTransport, **kwargs) -> GitHubRepoConnector:
    conn = GitHubRepoConnector(repos=["foo/bar"], **kwargs)
    # Swap the real client for one using our mock transport
    await conn._client.aclose()
    conn._client = httpx.AsyncClient(
        base_url="https://api.github.com",
        transport=transport,
        headers={"User-Agent": "test"},
    )
    return conn


async def test_list_resources_returns_configured_repos():
    transport = httpx.MockTransport(_make_handler({}))
    conn = await _make_connector(transport)
    try:
        res = await conn.list_resources()
        assert len(res) == 1
        assert res[0].id == "foo/bar"
        assert res[0].kind == "repo"
        assert res[0].extra["owner"] == "foo"
        assert res[0].extra["repo"] == "bar"
    finally:
        await conn.aclose()


async def test_initial_sync_filters_and_normalizes():
    head_sha = "abc1234567890123456789012345678901234567"
    tree_files = [
        ("vault/a.md", "blob_a"),
        ("vault/b.md", "blob_b"),
        ("vault/.obsidian/workspace.md", "blob_obs"),  # excluded
        ("vault/img.png", "blob_img"),  # wrong extension
        ("other/c.md", "blob_c"),  # wrong prefix
    ]
    routes = {
        ("GET", "/repos/foo/bar"): _mock_repo_response(),
        ("GET", "/repos/foo/bar/commits/main"): _mock_head_commit(head_sha),
        ("GET", f"/repos/foo/bar/git/trees/{head_sha}"): _mock_tree(head_sha, tree_files),
        ("GET", "/repos/foo/bar/contents/vault/a.md"): _mock_contents("vault/a.md", "blob_a", "# A\nbody"),
        ("GET", "/repos/foo/bar/contents/vault/b.md"): _mock_contents("vault/b.md", "blob_b", "# B\nbody"),
    }
    transport = httpx.MockTransport(_make_handler(routes))
    conn = await _make_connector(
        transport,
        path_prefixes=("vault/",),
        path_excludes=(".obsidian/",),
    )
    try:
        [res] = await conn.list_resources()
        events = await conn.fetch_since(res, after_external_id=None, limit=100)
        assert len(events) == 2
        paths = [e.payload["path"] for e in events]
        assert paths == ["vault/a.md", "vault/b.md"]  # sorted

        for e in events:
            assert e.source == "github"
            assert e.kind == "file_revision"
            assert e.payload["commit_sha"] == head_sha
            assert e.payload["change"] == "initial"
            assert e.external_id == f"{head_sha}::{e.payload['path']}"
            assert e.resource_id == "foo/bar"
            assert "content" in e.payload
    finally:
        await conn.aclose()


async def test_initial_sync_skips_oversize():
    head_sha = "abc1234567890123456789012345678901234567"
    routes = {
        ("GET", "/repos/foo/bar"): _mock_repo_response(),
        ("GET", "/repos/foo/bar/commits/main"): _mock_head_commit(head_sha),
        ("GET", f"/repos/foo/bar/git/trees/{head_sha}"): _mock_tree(
            head_sha, [("big.md", "blob_big")],
        ),
        ("GET", "/repos/foo/bar/contents/big.md"): {
            "path": "big.md", "sha": "blob_big",
            "size": 2_000_000, "encoding": "base64", "content": "",
        },
    }
    transport = httpx.MockTransport(_make_handler(routes))
    conn = await _make_connector(transport)
    try:
        [res] = await conn.list_resources()
        events = await conn.fetch_since(res, after_external_id=None, limit=100)
        assert events == []
    finally:
        await conn.aclose()


async def test_incremental_emits_per_commit_per_file():
    base = "0" * 40
    sha1 = "a" * 40
    sha2 = "b" * 40
    routes = {
        ("GET", "/repos/foo/bar"): _mock_repo_response(),
        ("GET", f"/repos/foo/bar/compare/{base}...main"): _mock_compare([
            {"sha": sha1, "commit": {"author": {"date": "2026-05-20T00:00:00Z"}}},
            {"sha": sha2, "commit": {"author": {"date": "2026-05-21T00:00:00Z"}}},
        ]),
        ("GET", f"/repos/foo/bar/commits/{sha1}"): _mock_commit_detail(
            sha1, [
                {"filename": "vault/x.md", "status": "added"},
                {"filename": "vault/notes.txt", "status": "added"},  # filtered out
            ],
            date="2026-05-20T00:00:00Z",
        ),
        ("GET", f"/repos/foo/bar/commits/{sha2}"): _mock_commit_detail(
            sha2, [
                {"filename": "vault/x.md", "status": "modified"},
                {"filename": "vault/y.md", "status": "renamed", "previous_filename": "vault/old.md"},
                {"filename": "vault/gone.md", "status": "removed"},
            ],
            date="2026-05-21T00:00:00Z",
        ),
        ("GET", "/repos/foo/bar/contents/vault/x.md"): _mock_contents("vault/x.md", "bx", "x content"),
        ("GET", "/repos/foo/bar/contents/vault/y.md"): _mock_contents("vault/y.md", "by", "y content"),
    }
    transport = httpx.MockTransport(_make_handler(routes))
    conn = await _make_connector(transport, path_prefixes=("vault/",))
    try:
        [res] = await conn.list_resources()
        events = await conn.fetch_since(
            res, after_external_id=f"{base}::seed", limit=100,
        )
        # sha1: x.md (added) — 1 event (txt filtered)
        # sha2: x.md (modified), y.md (renamed), gone.md (removed) — 3 events
        assert len(events) == 4
        kinds = [e.kind for e in events]
        assert kinds.count("file_revision") == 3
        assert kinds.count("file_delete") == 1
        # Watermark advances cleanly: last event's external_id starts with sha2
        assert events[-1].external_id.startswith(sha2)
        # Renamed file carries previous_path
        renamed = [e for e in events if e.payload.get("previous_path")]
        assert len(renamed) == 1
        assert renamed[0].payload["previous_path"] == "vault/old.md"
    finally:
        await conn.aclose()


async def test_incremental_404_returns_empty():
    """If base SHA isn't in the repo (history rewrite), return [] gracefully."""
    base = "deadbeef" * 5
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/foo/bar":
            return httpx.Response(200, json={"default_branch": "main"})
        if request.url.path.startswith("/repos/foo/bar/compare/"):
            return httpx.Response(404, json={"message": "no common ancestor"})
        return httpx.Response(404, json={"message": "?"})

    transport = httpx.MockTransport(handler)
    conn = await _make_connector(transport)
    try:
        [res] = await conn.list_resources()
        events = await conn.fetch_since(res, after_external_id=f"{base}::x", limit=10)
        assert events == []
    finally:
        await conn.aclose()


def test_constructor_requires_repos():
    with pytest.raises(ValueError):
        GitHubRepoConnector(repos=[])
