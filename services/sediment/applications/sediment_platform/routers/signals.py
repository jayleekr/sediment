"""POST /api/v1/signals/{kind} — implicit user signal writer.

Phase 2 of sediment#15 / §3 of sediment#16. Lightweight endpoint that
writes a single message_signals row. The expensive derived signals
(re_ask, dwell, session_end_*) are written by the cron in
`scripts/signal_derivation.py` — this endpoint is for things the
frontend knows directly: copy, cite_export, thumbs_*.

Auth: standard tenant_middleware (member must own a message in their
tenant). Rate-limited per member.

Bodies:
  POST /api/v1/signals/copy           { "message_id": "..." }
  POST /api/v1/signals/cite_export    { "message_id": "...", "ref": "..." }
  POST /api/v1/signals/thumbs_up      { "message_id": "..." }
  POST /api/v1/signals/thumbs_down    { "message_id": "...", "note": "..." }
"""
from __future__ import annotations
import json
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from lab_lib.db import app_session
from lab_lib.tenant_middleware import require_identity, Identity


router = APIRouter(prefix="/api/v1/signals", tags=["signals"])


# Kinds the FE can POST directly. Derived kinds (re_ask, dwell,
# session_end_*) are written only by the cron — exposing those here
# would let clients game the metrics.
_FE_KINDS = {"copy", "cite_export", "thumbs_up", "thumbs_down"}


class SignalReq(BaseModel):
    message_id: str = Field(..., description="UUID of the assistant message")
    value: Optional[float] = Field(None, description="Optional numeric value (dwell sec)")
    note: Optional[str] = Field(None, max_length=2000, description="Free-text annotation")
    meta: Optional[dict] = Field(default=None, description="JSON metadata blob")
    source: Optional[Literal["web", "cli", "mcp-shim"]] = Field(
        default="web", description="Where this signal originated"
    )


@router.post("/{kind}", status_code=204)
async def write_signal(
    kind: str,
    req: SignalReq,
    identity: Identity = Depends(require_identity),
):
    if kind not in _FE_KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"kind '{kind}' is not writable from frontend "
                   f"(allowed: {sorted(_FE_KINDS)})",
        )
    # Validate message exists + belongs to this tenant. tenant_middleware
    # set current_tenant_id via SET LOCAL, so this select is filtered
    # automatically by RLS — but we want a clean 404 instead of "silently
    # writes nothing then a phantom row".
    async with app_session(identity.tenant_id) as s:
        r = await s.execute(text("""
            SELECT 1 FROM messages
            WHERE id = CAST(:mid AS uuid)
              AND role = 'assistant'
              AND archived = false
        """), {"mid": req.message_id})
        if r.first() is None:
            raise HTTPException(status_code=404, detail="message_id not found")

        meta = req.meta or {}
        if req.note:
            meta["note"] = req.note[:2000]

        await s.execute(text("""
            INSERT INTO message_signals (
                tenant_id, message_id, kind, value, meta, source
            )
            VALUES (
                current_tenant_id(),
                CAST(:mid AS uuid),
                :kind,
                :val,
                CAST(:meta AS jsonb),
                :source
            )
        """), {
            "mid": req.message_id,
            "kind": kind,
            "val": req.value,
            "meta": json.dumps(meta),
            "source": req.source or "web",
        })
    return None
