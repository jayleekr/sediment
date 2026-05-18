"""Message feedback (👍/👎)."""
from __future__ import annotations
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from lab_lib.auth import Identity, require_identity
from lab_lib.db import app_session

router = APIRouter()


class FeedbackReq(BaseModel):
    message_id: str
    rating: int          # +1 or -1
    note: str | None = None


@router.post("")
async def post_feedback(req: FeedbackReq, identity: Identity = Depends(require_identity)):
    async with app_session(identity.tenant_id) as s:
        await s.execute(text("""
            INSERT INTO events (tenant_id, source, kind, member_id, payload)
            VALUES (current_tenant_id(), 'web', 'feedback.message', :mid, CAST(:p AS jsonb))
        """), {
            "mid": identity.member_id,
            "p": _json({"message_id": req.message_id, "rating": req.rating, "note": req.note}),
        })
    return {"ok": True}


def _json(d):
    import json
    return json.dumps(d)
