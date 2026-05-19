"""Vault freshness — browser-facing read of the last ingest.

The webhook (vault_ingester) writes a `vault.ingest` event on every push
re-ingest. This exposes it so the UI can show "updated N min ago" and a
stale warning — making a frozen vault VISIBLE instead of silently rotting
(ACTIVATION_ENGINE.md P1 freshness metric; the #1 thing that kills dogfood).
"""
from __future__ import annotations
import datetime as _dt

from fastapi import APIRouter, Depends
from sqlalchemy import text

from lab_lib.auth import Identity, require_identity
from lab_lib.db import app_session

router = APIRouter()

STALE_AFTER_SECONDS = 24 * 3600


@router.get("/freshness")
async def freshness(identity: Identity = Depends(require_identity)):
    async with app_session(identity.tenant_id) as s:
        r = await s.execute(text("""
            SELECT ts, payload FROM events
            WHERE kind = 'vault.ingest'
            ORDER BY ts DESC LIMIT 1
        """))
        row = r.first()
    if not row:
        return {
            "last_ingest_ts": None,
            "seconds_ago": None,
            "stale": True,
            "note": "no vault.ingest yet — feed pipeline not run",
        }
    ts = row[0]
    age = int((_dt.datetime.now(_dt.timezone.utc) - ts).total_seconds())
    return {
        "last_ingest_ts": ts.isoformat(),
        "seconds_ago": age,
        "stale": age > STALE_AFTER_SECONDS,
        "last": row[1],
    }
