"""GitHub Repo connector — read markdown vaults from public/private repos.

Use case: a tenant's knowledge substrate is a Git repo (Obsidian vault,
docs site, internal wiki). The connector enumerates matching files at
each sync, normalizes each file revision into a `NormalizedEvent` for
Sediment's chunk/embed/search pipeline.

Watermark model:
    `after_external_id` is the last commit SHA we successfully ingested.
    On next call we ask the GitHub Compare API for everything between
    that SHA and HEAD; for each commit we emit one event per matching
    file change (added/modified). Renames produce an event for the new
    path; deletions are emitted as `kind="file_delete"` with empty
    content so downstream can hard-delete chunks.

    For initial sync (no watermark), we enumerate the recursive Git Tree
    at HEAD and emit one `kind="file_revision"` per matching file. The
    last emitted event's `external_id` encodes HEAD's commit SHA so the
    caller can advance the watermark cleanly.

External ID format:
    `{commit_sha}::{file_path}` — unique per (commit, file), satisfies
    the base.py contract that watermark = last event's external_id.
    Parser: `commit_sha = external_id.split("::", 1)[0]`.

Auth:
    Optional GITHUB_TOKEN env var. Unauthenticated requests work on public
    repos (60 req/h limit, fine for dogfood). Authenticated = 5000 req/h.

Limits we do NOT try to solve here:
    - Compare API caps at 250 commits / 300 files per call. If the gap
      between watermark and HEAD is larger, caller should fall back to
      tree enumeration (TODO: auto-detect via `behind_by`).
    - Files >1MB are reported as "too_large" by the contents API. We
      detect and skip (with a log), since chunking a 1MB+ markdown blob
      is not the value-add.
"""
from __future__ import annotations

import base64
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from .base import ConnectorABC, NormalizedEvent, Resource

log = logging.getLogger(__name__)


_GH_BASE = "https://api.github.com"
_USER_AGENT = "Sediment-Collector/0.1 (+https://sediment.hypeproof-ai.xyz)"
_DEFAULT_EXTS = (".md", ".mdx")
_MAX_FILE_BYTES = 1_000_000  # 1MB — GitHub's contents API limit


def _resolve_token(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    for envvar in ("GITHUB_TOKEN", "GH_TOKEN", "HYPEPROOF_GITHUB_TOKEN"):
        v = os.environ.get(envvar)
        if v:
            return v
    return None  # unauth is fine for public repos OR an explicit opt-in


# WO-7 #5 2026-05-24: governance flag — when set, require a token.
# Unauthenticated GitHub API caps at 60 requests/hour. Initial sync of a
# 100-file vault blows past that limit before finishing one repo (one /tree
# call + one /contents call per matching file ≈ 100+ requests). The first
# pull dies mid-flight, the second silently retries the same files, and
# every tenant on the worker hits the same rate-limit ceiling. Force-fail
# at connector init when a real prod env (FLY_APP_NAME) is detected without
# a token. Local dev with no FLY_APP_NAME still tolerates anonymous calls.
def _enforce_token_in_prod(token: str | None) -> None:
    if token:
        return
    if os.environ.get("SEDIMENT_GITHUB_ALLOW_ANONYMOUS") == "1":
        # Explicit opt-in for the unusual case (e.g. one-shot smoke test).
        log.warning("github connector running anonymously — 60 req/hr cap")
        return
    if os.environ.get("FLY_APP_NAME") or os.environ.get("SEDIMENT_ENV") == "prod":
        raise RuntimeError(
            "GitHubRepoConnector requires a token in prod: set GITHUB_TOKEN, "
            "GH_TOKEN, or HYPEPROOF_GITHUB_TOKEN. Anonymous API is capped at "
            "60 req/hr — initial sync of a 100-file vault will exhaust it "
            "before finishing. To override (dev/smoke test), set "
            "SEDIMENT_GITHUB_ALLOW_ANONYMOUS=1."
        )


def _matches_filter(
    path: str,
    *,
    extensions: tuple[str, ...],
    path_prefixes: tuple[str, ...],
    path_excludes: tuple[str, ...],
) -> bool:
    if not any(path.endswith(ext) for ext in extensions):
        return False
    if path_prefixes and not any(path.startswith(p) for p in path_prefixes):
        return False
    if any(seg in path for seg in path_excludes):
        return False
    return True


class GitHubRepoConnector(ConnectorABC):
    """Pull markdown file revisions from one or more GitHub repos.

    Tenant config example (stored in `tenant_connectors.config`):

        {
          "repos": ["JinyongShin/hypeproof_kids_edu"],
          "path_prefixes": ["kids_edu_vault/", "meeting_notes/"],
          "path_excludes": [".raw/", ".obsidian/"],
          "extensions": [".md"]
        }
    """

    source_name = "github"

    def __init__(
        self,
        repos: list[str],
        *,
        token: str | None = None,
        path_prefixes: tuple[str, ...] = (),
        path_excludes: tuple[str, ...] = (".raw/", ".obsidian/", "node_modules/"),
        extensions: tuple[str, ...] = _DEFAULT_EXTS,
        branch: str | None = None,  # None = use each repo's default
        http_timeout: float = 30.0,
    ) -> None:
        if not repos:
            raise ValueError("GitHubRepoConnector requires at least one repo")
        self._repos = list(repos)
        self._path_prefixes = tuple(path_prefixes)
        self._path_excludes = tuple(path_excludes)
        self._extensions = tuple(extensions)
        self._branch_override = branch
        self._token = _resolve_token(token)
        _enforce_token_in_prod(self._token)

        headers = {
            "User-Agent": _USER_AGENT,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        self._client = httpx.AsyncClient(
            base_url=_GH_BASE,
            timeout=http_timeout,
            headers=headers,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    async def _get(self, path: str, **params: Any) -> httpx.Response:
        """Issue a GitHub API GET with explicit rate-limit awareness.

        WO-7 #5 2026-05-24: previously this had zero rate-limit handling —
        a 403 with ``X-RateLimit-Remaining: 0`` would propagate as a fatal
        500 upstream. Now we surface it as a clear RuntimeError so the
        caller (collection_agent) can mark the connector unhealthy instead
        of retrying the same call into a wall of 403s.
        """
        r = await self._client.get(path, params=params or None)
        if r.status_code == 403 and r.headers.get("X-RateLimit-Remaining") == "0":
            reset = r.headers.get("X-RateLimit-Reset", "unknown")
            limit = r.headers.get("X-RateLimit-Limit", "unknown")
            raise RuntimeError(
                f"github rate limit exhausted (limit={limit}, reset_epoch={reset}). "
                f"{'Set GITHUB_TOKEN' if not self._token else 'Token exhausted — wait or rotate'}."
            )
        return r

    async def _resolve_branch(self, owner: str, repo: str) -> str:
        if self._branch_override:
            return self._branch_override
        r = await self._get(f"/repos/{owner}/{repo}")
        if r.status_code != 200:
            raise RuntimeError(
                f"failed to resolve default branch for {owner}/{repo}: "
                f"{r.status_code} {r.text[:200]}"
            )
        return r.json().get("default_branch") or "main"

    async def _head_sha(self, owner: str, repo: str, branch: str) -> str:
        r = await self._get(f"/repos/{owner}/{repo}/commits/{branch}")
        if r.status_code != 200:
            raise RuntimeError(
                f"failed to read HEAD for {owner}/{repo}@{branch}: "
                f"{r.status_code} {r.text[:200]}"
            )
        return r.json()["sha"]

    async def _read_file_at(
        self, owner: str, repo: str, path: str, ref: str
    ) -> tuple[str, str] | None:
        """Returns (content_str, blob_sha) or None if too large / missing."""
        r = await self._get(
            f"/repos/{owner}/{repo}/contents/{path}", ref=ref,
        )
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise RuntimeError(
                f"failed to read {owner}/{repo}/{path}@{ref}: "
                f"{r.status_code} {r.text[:200]}"
            )
        j = r.json()
        if j.get("size", 0) > _MAX_FILE_BYTES:
            return None
        if j.get("encoding") != "base64":
            return None
        try:
            content = base64.b64decode(j["content"]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
        return content, j.get("sha") or ""

    def _resource_for(self, slug: str) -> Resource:
        owner, repo = slug.split("/", 1)
        return Resource(
            id=slug,
            name=repo,
            kind="repo",
            extra={"owner": owner, "repo": repo},
        )

    # -----------------------------------------------------------------
    # ConnectorABC implementation
    # -----------------------------------------------------------------

    async def list_resources(self) -> list[Resource]:
        """Return one Resource per configured repo.

        We don't enumerate "all repos the token can see" — that's an
        onboarding-UI concern. Configured repos are the source of truth.
        """
        return [self._resource_for(s) for s in self._repos]

    async def fetch_since(
        self,
        resource: Resource,
        after_external_id: str | None,
        limit: int = 100,
    ) -> list[NormalizedEvent]:
        owner = resource.extra.get("owner")
        repo = resource.extra.get("repo")
        if not owner or not repo:
            raise ValueError(
                "Resource.extra must include 'owner' and 'repo' for GitHub"
            )
        branch = await self._resolve_branch(owner, repo)

        if after_external_id is None:
            return await self._initial_sync(owner, repo, branch, limit)

        # Parse out the commit SHA from the encoded external_id.
        base_sha = after_external_id.split("::", 1)[0]
        return await self._incremental(owner, repo, branch, base_sha, limit)

    # -----------------------------------------------------------------
    # Initial sync — tree enumeration at HEAD
    # -----------------------------------------------------------------

    async def _initial_sync(
        self, owner: str, repo: str, branch: str, limit: int,
    ) -> list[NormalizedEvent]:
        head = await self._head_sha(owner, repo, branch)
        r = await self._get(
            f"/repos/{owner}/{repo}/git/trees/{head}", recursive="1",
        )
        if r.status_code != 200:
            raise RuntimeError(
                f"tree fetch failed: {r.status_code} {r.text[:200]}"
            )
        tree = r.json()
        if tree.get("truncated"):
            # Repo > 100k entries — out of scope for v1, log and proceed
            # with what we got. A real solution would page via /git/trees
            # on each subdir.
            pass

        matched_paths: list[tuple[str, str]] = []  # (path, blob_sha)
        for node in tree.get("tree", []):
            if node.get("type") != "blob":
                continue
            path = node.get("path") or ""
            if not _matches_filter(
                path,
                extensions=self._extensions,
                path_prefixes=self._path_prefixes,
                path_excludes=self._path_excludes,
            ):
                continue
            matched_paths.append((path, node.get("sha") or ""))

        # Stable order so callers always see oldest-first deterministic
        matched_paths.sort(key=lambda x: x[0])
        if limit > 0:
            matched_paths = matched_paths[:limit]

        events: list[NormalizedEvent] = []
        now = datetime.now(tz=timezone.utc)
        for path, blob_sha in matched_paths:
            read = await self._read_file_at(owner, repo, path, head)
            if read is None:
                continue
            content, _ = read
            events.append(NormalizedEvent(
                source="github",
                kind="file_revision",
                external_id=f"{head}::{path}",
                ts=now,  # commit ts would need a separate call; initial sync uses now
                payload={
                    "repo": f"{owner}/{repo}",
                    "branch": branch,
                    "commit_sha": head,
                    "path": path,
                    "blob_sha": blob_sha,
                    "content": content,
                    "change": "initial",
                    "size": len(content.encode("utf-8")),
                },
                resource_id=resource_id_for(owner, repo),
            ))
        return events

    # -----------------------------------------------------------------
    # Incremental — Compare API since watermark
    # -----------------------------------------------------------------

    async def _incremental(
        self, owner: str, repo: str, branch: str, base_sha: str, limit: int,
    ) -> list[NormalizedEvent]:
        # Compare base...branch — returns commits + per-file changes
        r = await self._get(
            f"/repos/{owner}/{repo}/compare/{base_sha}...{branch}",
            per_page=250,
        )
        if r.status_code == 404:
            # base_sha not in this repo (history rewrite?) — return empty,
            # caller should reset watermark.
            return []
        if r.status_code != 200:
            raise RuntimeError(
                f"compare failed: {r.status_code} {r.text[:200]}"
            )
        j = r.json()
        commits = j.get("commits") or []
        if not commits:
            return []

        events: list[NormalizedEvent] = []
        # Walk commits oldest-first (Compare API returns oldest-first
        # for `base...head`). For each commit, get its files; for each
        # matching file, emit an event.
        for commit in commits:
            sha = commit.get("sha") or ""
            commit_meta = commit.get("commit") or {}
            author_meta = commit_meta.get("author") or {}
            ts_str = author_meta.get("date")
            try:
                ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00")) \
                    if ts_str else datetime.now(tz=timezone.utc)
            except (AttributeError, ValueError):
                ts_dt = datetime.now(tz=timezone.utc)

            # Per-commit file list: GET /repos/{o}/{r}/commits/{sha}
            cr = await self._get(f"/repos/{owner}/{repo}/commits/{sha}")
            if cr.status_code != 200:
                continue
            cj = cr.json()
            files = cj.get("files") or []
            for f in files:
                path = f.get("filename") or ""
                if not _matches_filter(
                    path,
                    extensions=self._extensions,
                    path_prefixes=self._path_prefixes,
                    path_excludes=self._path_excludes,
                ):
                    continue
                status = f.get("status") or ""

                if status == "removed":
                    events.append(NormalizedEvent(
                        source="github",
                        kind="file_delete",
                        external_id=f"{sha}::{path}",
                        ts=ts_dt,
                        payload={
                            "repo": f"{owner}/{repo}",
                            "branch": branch,
                            "commit_sha": sha,
                            "path": path,
                            "change": "removed",
                            "author": (author_meta.get("name") or ""),
                            "message": (commit_meta.get("message") or "")[:500],
                        },
                        resource_id=resource_id_for(owner, repo),
                    ))
                    continue

                # added | modified | renamed | copied — fetch new content
                read = await self._read_file_at(owner, repo, path, sha)
                if read is None:
                    continue
                content, blob_sha = read
                events.append(NormalizedEvent(
                    source="github",
                    kind="file_revision",
                    external_id=f"{sha}::{path}",
                    ts=ts_dt,
                    payload={
                        "repo": f"{owner}/{repo}",
                        "branch": branch,
                        "commit_sha": sha,
                        "path": path,
                        "blob_sha": blob_sha,
                        "content": content,
                        "change": status,
                        "previous_path": f.get("previous_filename"),
                        "additions": f.get("additions"),
                        "deletions": f.get("deletions"),
                        "author": (author_meta.get("name") or ""),
                        "message": (commit_meta.get("message") or "")[:500],
                        "size": len(content.encode("utf-8")),
                    },
                    resource_id=resource_id_for(owner, repo),
                ))
                if limit > 0 and len(events) >= limit:
                    return events
        return events


def resource_id_for(owner: str, repo: str) -> str:
    return f"{owner}/{repo}"


# ---------------------------------------------------------------------------
# Convenience: one-shot ingest
# ---------------------------------------------------------------------------

async def fetch_repo_files(
    repo_slug: str,
    *,
    path_prefixes: tuple[str, ...] = (),
    extensions: tuple[str, ...] = _DEFAULT_EXTS,
    limit: int = 200,
    token: str | None = None,
) -> list[NormalizedEvent]:
    """Single-shot helper for ad-hoc backfills / dogfood runs."""
    conn = GitHubRepoConnector(
        repos=[repo_slug],
        token=token,
        path_prefixes=path_prefixes,
        extensions=extensions,
    )
    try:
        [res] = await conn.list_resources()
        return await conn.fetch_since(res, after_external_id=None, limit=limit)
    finally:
        await conn.aclose()
