"""GitHub repo fetcher — pulls markdown file revisions into events + chunks.

Analog of `scripts/discord_fetch.py` but for GitHub repos via the
`GitHubRepoConnector`. Reads integration rows (kind='github') from the DB,
runs the connector against each, INSERTs raw events for audit, and POSTs
file content to vault_ingester `/v1/ingest/document` so the same
chunking+embedding pipeline applies.

Usage:
    # Pull all github integrations on schedule (the cron path):
    python -m scripts.github_repo_fetch --all

    # Pull one tenant only (dev / on-demand):
    python -m scripts.github_repo_fetch --tenant kids-edu

    # Dry-run: no writes, prints what would happen
    python -m scripts.github_repo_fetch --tenant kids-edu --dry-run

    # Force full re-sync (ignore watermark — useful after big-bang refactors):
    python -m scripts.github_repo_fetch --tenant kids-edu --reset-watermark

Watermark:
    Stored in `integrations.config.state.head_sha`. On a successful run,
    the script records the HEAD SHA at fetch time (not the last event's
    SHA — this advances past code-only commits that produced no markdown
    events, avoiding redundant re-walks).

Exit codes:
    0  ok (or no work)
    1  config error (no integration / DB down / bad config)
    2  partial (some events failed insert; check logs)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text

SCRIPT = Path(__file__).resolve()
sys.path.insert(0, str(SCRIPT.parents[1]))  # services/sediment/

from lab_lib.collection_agent import decide  # noqa: E402
from lab_lib.connectors.github_repo import (  # noqa: E402
    GitHubRepoConnector,
    NormalizedEvent,
)
from lab_lib.db import service_session  # noqa: E402
from lab_lib.logging import configure_logging, get_logger  # noqa: E402

configure_logging()
log = get_logger("github_repo_fetch")


# ---------------------------------------------------------------------------
# DB helpers — integrations row I/O
# ---------------------------------------------------------------------------

async def _load_integrations(tenant_slug: str | None) -> list[dict]:
    """Return [{integration_id, tenant_id, tenant_slug, config}]."""
    sql = """
        SELECT i.id::text, i.tenant_id::text, t.slug, i.config
        FROM integrations i
        JOIN tenants t ON t.id = i.tenant_id
        WHERE i.kind = 'github'
    """
    params: dict[str, Any] = {}
    if tenant_slug:
        sql += " AND t.slug = :slug"
        params["slug"] = tenant_slug
    async with service_session() as s:
        r = await s.execute(text(sql), params)
        return [
            {"id": row[0], "tenant_id": row[1], "slug": row[2], "config": row[3] or {}}
            for row in r.fetchall()
        ]


async def _save_state(integration_id: str, new_state: dict) -> None:
    async with service_session() as s:
        await s.execute(text("""
            UPDATE integrations
            SET config = jsonb_set(config, '{state}', CAST(:st AS jsonb), true),
                last_sync_at = now()
            WHERE id = CAST(:id AS uuid)
        """), {"id": integration_id, "st": json.dumps(new_state)})
        await s.commit()


async def _insert_event(tenant_id: str, ev: NormalizedEvent) -> bool:
    """Insert one event. Dedup on (tenant_id, source='github', payload.external_id)."""
    async with service_session() as s:
        existing = await s.execute(text("""
            SELECT 1 FROM events
            WHERE tenant_id = CAST(:tid AS uuid) AND source = 'github'
              AND payload->>'external_id' = :ext LIMIT 1
        """), {"tid": tenant_id, "ext": ev.external_id})
        if existing.first():
            return False
        payload = dict(ev.payload)
        payload["external_id"] = ev.external_id  # promote to first-class for dedup
        await s.execute(text("""
            INSERT INTO events (tenant_id, source, kind, payload, ts)
            VALUES (CAST(:tid AS uuid), 'github', :kind, CAST(:payload AS jsonb), :ts)
        """), {
            "tid": tenant_id,
            "kind": ev.kind,
            "payload": json.dumps(payload, default=str),
            "ts": ev.ts,
        })
        await s.commit()
        return True


async def _delete_artifact_chunks(tenant_id: str, ref: str) -> int:
    """Hard-delete chunks for a removed file (and the artifact)."""
    async with service_session() as s:
        await s.execute(text("SELECT set_config('app.tenant_id', :tid, true)"),
                        {"tid": tenant_id})
        r = await s.execute(text("""
            DELETE FROM artifacts WHERE tenant_id = CAST(:tid AS uuid) AND ref = :ref
        """), {"tid": tenant_id, "ref": ref})
        await s.commit()
        return r.rowcount


# ---------------------------------------------------------------------------
# vault_ingester client (in-process HTTP to localhost:8000)
# ---------------------------------------------------------------------------

INGESTER_BASE = os.environ.get(
    "VAULT_INGESTER_BASE",
    f"http://127.0.0.1:{os.environ.get('VAULT_INGESTER_PORT', '11000')}",
)


async def _ingest_doc(
    http: httpx.AsyncClient,
    tenant_id: str,
    ref: str,
    body: str,
    doc_type: str = "wiki",
) -> tuple[bool, str]:
    try:
        r = await http.post(
            f"{INGESTER_BASE}/v1/ingest/document",
            json={"tenant_id": tenant_id, "ref": ref, "type": doc_type, "body": body},
            timeout=60.0,
        )
        if r.status_code == 200:
            return True, ""
        return False, f"http {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, str(e)[:200]


# ---------------------------------------------------------------------------
# Main per-integration flow
# ---------------------------------------------------------------------------

def _classify_doc_type(path: str) -> str:
    """Pick artifact `type` from path heuristics. MUST return a value in
    init.sql's CHECK constraint:
      column | research | novel | note | decision | meeting | message | event
    """
    p = path.lower()
    if "/meeting" in p or "meeting" in Path(p).stem or "meeting_notes/" in p:
        return "meeting"
    if "/decisions/" in p or "/adr" in p:
        return "decision"
    if "/research" in p:
        return "research"
    # specs / concepts / runbooks / intel / stakeholders / comms / etc all
    # collapse to "note" — there's no finer-grained category in the schema
    # that fits, and adding one is a DDL change (out of scope).
    return "note"


async def _process_integration(
    integration: dict,
    *,
    dry_run: bool,
    reset_watermark: bool,
) -> dict:
    cfg = dict(integration["config"])
    state = dict(cfg.get("state") or {})
    prev_head = None if reset_watermark else state.get("head_sha")

    repos = cfg.get("repos") or []
    if not repos:
        return {"slug": integration["slug"], "skipped": "no_repos"}

    conn = GitHubRepoConnector(
        repos=repos,
        path_prefixes=tuple(cfg.get("path_prefixes") or ()),
        path_excludes=tuple(cfg.get("path_excludes") or (".raw/", ".obsidian/", "node_modules/")),
        extensions=tuple(cfg.get("extensions") or (".md", ".mdx")),
        branch=cfg.get("branch"),
    )

    summary: dict[str, Any] = {
        "slug": integration["slug"],
        "tenant_id": integration["tenant_id"],
        "repos": repos,
        "prev_head": prev_head,
        "events_inserted": 0,
        "events_skipped_dup": 0,
        "files_ingested": 0,
        "files_deleted": 0,
        "ingest_failures": [],
        "dry_run": dry_run,
    }

    http = httpx.AsyncClient()
    try:
        for resource in await conn.list_resources():
            # Fetch with a generous per-call limit; the script may be called
            # hourly so 500 / call easily covers the bursty days.
            try:
                events = await conn.fetch_since(
                    resource, after_external_id=prev_head, limit=500,
                )
            except RuntimeError as e:
                log.warning("github.fetch.failed", repo=resource.id, err=str(e)[:200])
                summary.setdefault("fetch_errors", []).append(
                    {"repo": resource.id, "err": str(e)[:200]}
                )
                continue

            log.info("github.fetch.ok",
                     repo=resource.id, prev_head=prev_head, events=len(events))

            if dry_run:
                for ev in events[:5]:
                    p = ev.payload
                    print(f"  [{ev.kind:14s}] {p.get('commit_sha','')[:7]} {p.get('path','')[:70]}")
                summary["events_inserted"] += len(events)  # would-be
                continue

            for ev in events:
                # 0) Ask the Collection AI Agent what to do with this event.
                #    `decide()` reads source_kind + event_rules from cfg and
                #    returns whether to ingest, notify, or both.
                cd = decide(ev, cfg)

                # 1) Raw event row (audit / replay) — ALWAYS recorded, even
                #    when decide() says skip, so the audit trail is complete.
                #    Dedup'd on external_id; duplicates fall through to step 2.
                try:
                    if await _insert_event(integration["tenant_id"], ev):
                        summary["events_inserted"] += 1
                    else:
                        summary["events_skipped_dup"] += 1
                except Exception as e:
                    log.warning("github.event.insert_failed",
                                ext=ev.external_id, err=str(e)[:200])
                    summary["ingest_failures"].append(
                        {"ext": ev.external_id, "err": str(e)[:200]}
                    )
                    continue

                # 2) Delete first if event is a delete-kind. decide() returns
                #    {ingest: False, notify: False} on file_delete (per vault
                #    rules); the actual hard-delete is the script's job.
                path = ev.payload.get("path") or ""
                if ev.kind == "file_delete":
                    n = await _delete_artifact_chunks(integration["tenant_id"], path)
                    summary["files_deleted"] += 1
                    log.info("github.artifact.deleted", ref=path, chunks=n,
                             matched_rule=cd.matched_rule)
                    continue

                # 3) Ingest (chunk + embed via vault_ingester) if the agent
                #    said so. Empty content → skip (some events carry only
                #    metadata, no body).
                if cd.ingest:
                    content = ev.payload.get("content")
                    if content:
                        ok, err = await _ingest_doc(
                            http,
                            integration["tenant_id"],
                            ref=path,
                            body=content,
                            doc_type=_classify_doc_type(path),
                        )
                        if ok:
                            summary["files_ingested"] += 1
                        else:
                            log.warning("github.ingest.failed", ref=path, err=err,
                                        matched_rule=cd.matched_rule)
                            summary["ingest_failures"].append({"ref": path, "err": err})

                # 4) Notify if the agent said so. This is where new_decision
                #    notifications come from when a wiki/decisions/*.md lands.
                if cd.notify:
                    log.info("github.notify.triggered",
                             event_type=cd.notify_event_type,
                             channels=cd.notify_channels,
                             ref=path,
                             matched_rule=cd.matched_rule)
                    # Notification dispatch goes through the same path
                    # scheduler.py uses — see _send_notify there. Not wired in
                    # this script yet because we want the design fully
                    # land-tested via consolidate_memory.py first; wiring here
                    # is a 5-line addition once that path is mature.
                    summary.setdefault("notify_queued", []).append({
                        "event_type": cd.notify_event_type,
                        "ref": path,
                        "channels": cd.notify_channels,
                    })

        # Advance watermark — use the LAST event's commit_sha if we have
        # any; otherwise leave it untouched (no work happened).
        if not dry_run:
            # Re-query HEAD via the connector's helper for race-free advance
            try:
                # Compute per-repo HEAD now; store the latest one we saw.
                # For single-repo integrations (the dogfood case) this is
                # just one query.
                final_heads = {}
                for resource in await conn.list_resources():
                    owner = resource.extra["owner"]
                    repo = resource.extra["repo"]
                    branch = await conn._resolve_branch(owner, repo)
                    final_heads[resource.id] = await conn._head_sha(owner, repo, branch)
                # If there's only one repo, use its head SHA directly.
                # If multiple, pick the lexicographically-last (good enough
                # for v1 — multi-repo per integration is post-MVP).
                if final_heads:
                    new_head = sorted(final_heads.values())[-1]
                    state["head_sha"] = new_head
                    state["last_heads_per_repo"] = final_heads
                    await _save_state(integration["id"], state)
                    summary["new_head"] = new_head
            except Exception as e:
                log.warning("github.watermark.update_failed", err=str(e)[:200])
                summary["watermark_error"] = str(e)[:200]
    finally:
        await http.aclose()
        await conn.aclose()

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true",
                   help="process all github integrations (the cron path)")
    g.add_argument("--tenant", help="process one tenant by slug")
    p.add_argument("--dry-run", action="store_true",
                   help="no writes; print what would happen")
    p.add_argument("--reset-watermark", action="store_true",
                   help="ignore stored head_sha; re-do initial sync")
    return p


async def amain(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    integrations = await _load_integrations(
        tenant_slug=None if args.all else args.tenant
    )
    if not integrations:
        msg = "no github integrations found"
        if args.tenant:
            msg += f" for tenant={args.tenant}"
        print(f"WARN: {msg}", file=sys.stderr)
        return 0

    overall: list[dict] = []
    for integ in integrations:
        log.info("github.sync.start",
                 slug=integ["slug"], integration_id=integ["id"])
        try:
            result = await _process_integration(
                integ, dry_run=args.dry_run, reset_watermark=args.reset_watermark,
            )
            overall.append(result)
            log.info("github.sync.done", **{
                k: v for k, v in result.items()
                if k not in ("ingest_failures", "fetch_errors")
            })
        except Exception as e:
            log.exception("github.sync.failed", slug=integ["slug"])
            overall.append({"slug": integ["slug"], "error": str(e)[:300]})

    print(json.dumps(overall, indent=2, ensure_ascii=False, default=str))
    any_failure = any(
        r.get("ingest_failures") or r.get("error") or r.get("fetch_errors")
        for r in overall
    )
    return 2 if any_failure else 0


def main():
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
