"""Vault freshness — browser-facing read of the capture/index pipeline.

Freshness is not a single timestamp. A healthy vault needs fresh source
events, artifact upserts, chunks, and distilled decisions. Returning the axes
separately makes a broken stage visible instead of collapsing everything into
one misleading "updated N ago" badge.
"""
from __future__ import annotations
import datetime as _dt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from lab_lib.auth import Identity, require_identity
from lab_lib.context_packs import parse_scope, read_pack, rebuild_packs
from lab_lib.db import app_session

router = APIRouter()

STALE_AFTER_SECONDS = 24 * 3600
DISCORD_STALE_AFTER_SECONDS = 24 * 3600
GITHUB_STALE_AFTER_SECONDS = 24 * 3600
ARTIFACT_STALE_AFTER_SECONDS = 24 * 3600
CHUNK_STALE_AFTER_SECONDS = 24 * 3600
DECISION_STALE_AFTER_SECONDS = 7 * 24 * 3600


def _parse_ts(ts: Any) -> _dt.datetime | None:
    if ts is None:
        return None
    if isinstance(ts, _dt.datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=_dt.timezone.utc)
    if isinstance(ts, str):
        try:
            parsed = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            return None
    return None


def _iso(ts: Any) -> str | None:
    parsed = _parse_ts(ts)
    return parsed.isoformat() if parsed else None


def _age(now: _dt.datetime, ts: Any) -> int | None:
    parsed = _parse_ts(ts)
    if parsed is None:
        return None
    return int((now - parsed).total_seconds())


def _signal(now: _dt.datetime, ts: Any, stale_after: int, *, payload: Any = None) -> dict:
    seconds_ago = _age(now, ts)
    return {
        "ts": _iso(ts),
        "seconds_ago": seconds_ago,
        "stale": seconds_ago is None or seconds_ago > stale_after,
        "payload": payload,
    }


@router.get("/freshness")
async def freshness(identity: Identity = Depends(require_identity)):
    async with app_session(identity.tenant_id) as s:
        r = await s.execute(text("""
            SELECT
              (SELECT jsonb_build_object('ts', e.ts, 'payload', e.payload)
                FROM events e
                WHERE e.kind IN ('vault.ingest', 'vault.sync')
                  AND e.tenant_id = current_tenant_id()
                ORDER BY e.ts DESC LIMIT 1) AS vault_sync,
              (SELECT max(e.ts) FROM events e
                WHERE e.source = 'github' AND e.tenant_id = current_tenant_id()) AS github_event_ts,
              (SELECT max(e.ts) FROM events e
                WHERE e.source = 'discord' AND e.tenant_id = current_tenant_id()) AS discord_event_ts,
              (SELECT max(a.updated_at) FROM artifacts a
                WHERE a.tenant_id = current_tenant_id()) AS artifact_update_ts,
              (SELECT max(c.created_at) FROM chunks c
                WHERE c.tenant_id = current_tenant_id()) AS chunk_update_ts,
              (SELECT max(d.created_at) FROM decisions d
                WHERE d.tenant_id = current_tenant_id()) AS decision_ts,
              (SELECT max(a.updated_at) FROM artifacts a
                WHERE a.type = 'decision' AND a.tenant_id = current_tenant_id()) AS decision_artifact_ts,
              (SELECT count(*) FROM artifacts a
                WHERE a.tenant_id = current_tenant_id()) AS artifact_count,
              (SELECT count(*) FROM chunks c
                WHERE c.tenant_id = current_tenant_id()) AS chunk_count,
              (SELECT count(*) FROM decisions d
                WHERE d.tenant_id = current_tenant_id()) AS decision_count
        """))
        row = dict((r.first() or {})._mapping)

    now = _dt.datetime.now(_dt.timezone.utc)
    vault_sync = row.get("vault_sync") or {}
    if isinstance(vault_sync, str):
        import json as _json
        vault_sync = _json.loads(vault_sync)

    signals = {
        "vault_sync": _signal(
            now, vault_sync.get("ts"), ARTIFACT_STALE_AFTER_SECONDS,
            payload=vault_sync.get("payload"),
        ),
        "github_event": _signal(now, row.get("github_event_ts"), GITHUB_STALE_AFTER_SECONDS),
        "discord_event": _signal(now, row.get("discord_event_ts"), DISCORD_STALE_AFTER_SECONDS),
        "artifact_update": _signal(now, row.get("artifact_update_ts"), ARTIFACT_STALE_AFTER_SECONDS),
        "chunk_update": _signal(now, row.get("chunk_update_ts"), CHUNK_STALE_AFTER_SECONDS),
        "decision": _signal(now, row.get("decision_ts"), DECISION_STALE_AFTER_SECONDS),
        "decision_artifact": _signal(
            now, row.get("decision_artifact_ts"), DECISION_STALE_AFTER_SECONDS,
        ),
    }
    violations = [
        name for name, sig in signals.items()
        if sig["stale"] and name in {"vault_sync", "artifact_update", "chunk_update"}
    ]
    # If artifacts exist but chunks do not, retrieval cannot cite the vault.
    if int(row.get("artifact_count") or 0) > 0 and int(row.get("chunk_count") or 0) == 0:
        violations.append("artifact_without_chunks")

    latest_ts = max(
        (_parse_ts(ts) for ts in [
            vault_sync.get("ts"),
            row.get("artifact_update_ts"),
            row.get("chunk_update_ts"),
            row.get("decision_artifact_ts"),
        ] if _parse_ts(ts) is not None),
        default=None,
    )
    latest_age = _age(now, latest_ts)
    return {
        # Backwards-compatible fields consumed by the current badge.
        "last_ingest_ts": _iso(latest_ts),
        "seconds_ago": latest_age,
        "stale": bool(violations),
        "last": vault_sync.get("payload"),
        # Reliability-grade payload.
        "signals": signals,
        "counts": {
            "artifacts": int(row.get("artifact_count") or 0),
            "chunks": int(row.get("chunk_count") or 0),
            "decisions": int(row.get("decision_count") or 0),
        },
        "slo": {
            "vault_sync_seconds": ARTIFACT_STALE_AFTER_SECONDS,
            "github_event_seconds": GITHUB_STALE_AFTER_SECONDS,
            "discord_event_seconds": DISCORD_STALE_AFTER_SECONDS,
            "artifact_update_seconds": ARTIFACT_STALE_AFTER_SECONDS,
            "chunk_update_seconds": CHUNK_STALE_AFTER_SECONDS,
            "decision_seconds": DECISION_STALE_AFTER_SECONDS,
        },
        "violations": sorted(set(violations)),
    }


# ============================================================
# Context packs (sediment#142)
# ============================================================

@router.get("/context")
async def context_pack(
    kind: str = Query(default="hot", pattern="^(hot|index)$"),
    scope: str = Query(default="tenant"),
    identity: Identity = Depends(require_identity),
):
    """Read a pre-built context pack — the cheap thing a session reads first.

    `scope=me` resolves to this member's own pack. Any other `member:<uuid>` is
    refused: a pack is built through that member's visibility, so handing one
    to somebody else would launder exactly the boundary #140 established.
    """
    if scope == "me":
        scope = f"member:{identity.member_id}"
    try:
        scope_kind, value = parse_scope(scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if scope_kind == "member" and value != identity.member_id:
        raise HTTPException(
            status_code=403,
            detail="a member-scoped pack is readable only by that member")

    async with app_session(identity.tenant_id) as s:
        pack = await read_pack(s, identity.tenant_id, scope, kind)
        if pack is None:
            # Build on demand rather than 404. An absent pack means "not
            # generated yet", which is an implementation detail the caller
            # cannot act on.
            built = await rebuild_packs(s, identity.tenant_id, scope)
            pack = next(
                ({"scope_key": p.scope_key, "kind": p.kind, "body": p.body,
                  "token_estimate": p.token_estimate, "sources": p.sources,
                  "updated_at": None}
                 for p in built if p.kind == kind), None)
        if pack is None:
            raise HTTPException(status_code=503,
                                detail="context pack could not be generated")
    return pack


@router.post("/context/rebuild")
async def context_pack_rebuild(
    scope: str = Query(default="tenant"),
    identity: Identity = Depends(require_identity),
):
    """Force a rebuild. Ingestion already triggers this; the endpoint is for
    when someone needs the pack to reflect a change *now*."""
    if scope == "me":
        scope = f"member:{identity.member_id}"
    try:
        scope_kind, value = parse_scope(scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if scope_kind == "member" and value != identity.member_id:
        raise HTTPException(status_code=403, detail="not your pack")

    async with app_session(identity.tenant_id) as s:
        packs = await rebuild_packs(s, identity.tenant_id, scope)
    return {"scope": scope,
            "rebuilt": [{"kind": p.kind, "token_estimate": p.token_estimate}
                        for p in packs]}
