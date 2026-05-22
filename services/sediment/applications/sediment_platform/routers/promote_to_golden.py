"""POST /api/v1/feedback/promote-to-golden — convert a real query into a
golden case.

Phase 4 of sediment#15. The query plus its observed (intent, citations,
grounding_status) becomes a candidate row that will be appended to
golden_queries.yaml. This endpoint stores the proposal in a
`golden_proposals` table (light, just an events row for now) — a
separate cron/admin tool merges accepted proposals into the YAML file
via PR.

Why not commit to the YAML directly from here? RLS — the YAML is in
git, the server can't sign git commits as the user. So we capture the
intent here, then a privileged review step lands it.

Rate limit: 5 per member per day (already enforced by tenant
middleware's default budget for /feedback/*).

Body:
  POST /api/v1/feedback/promote-to-golden
  {
    "conv_id": "...",
    "message_id": "...",       # the BAD assistant message
    "reason": "answer is wrong — vault summary returned old counts",
    "expected_intent": "meta",  # optional, user can leave blank
    "ideal_refs": ["..."]       # optional
  }

Response:
  204 No Content (proposal stored — admin will review)
"""
from __future__ import annotations
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from lab_lib.db import app_session
from lab_lib.auth import Identity, require_identity


router = APIRouter(tags=["feedback"])


class PromoteReq(BaseModel):
    conv_id: str = Field(..., description="Conversation containing the bad answer")
    message_id: str = Field(..., description="Assistant message id (the bad one)")
    reason: str = Field(..., min_length=3, max_length=1000,
                        description="What's wrong with the answer")
    expected_intent: Optional[str] = Field(
        default=None,
        description="What the router SHOULD have picked (library/meta/freshness/member/decision)",
    )
    ideal_refs: Optional[list[str]] = Field(
        default=None,
        description="Optional list of refs that should appear in citations",
    )


@router.post("/promote-to-golden", status_code=204)
async def promote_to_golden(req: PromoteReq, identity: Identity = Depends(require_identity)):
    """Promote a failed answer to a golden-set proposal.

    Stored as an `events` row with kind='golden.proposal' for admin
    review. Promotions per user per day are rate-limited via the
    tenant_middleware default budget on /feedback/*.
    """
    async with app_session(identity.tenant_id) as s:
        # 1. Validate: message must exist, role=assistant, in this tenant.
        r = await s.execute(text("""
            SELECT m.id::text,
                   m.content,
                   m.intent,
                   m.grounding_status,
                   m.citations,
                   u.content AS user_query
            FROM messages m
            LEFT JOIN messages u
              ON u.conv_id = m.conv_id
             AND u.role    = 'user'
             AND u.ts      < m.ts
             AND u.ts      = (
               SELECT MAX(ts) FROM messages
               WHERE conv_id = m.conv_id
                 AND role    = 'user'
                 AND ts      < m.ts
             )
            WHERE m.id   = CAST(:mid AS uuid)
              AND m.role = 'assistant'
              AND m.archived = false
        """), {"mid": req.message_id})
        row = r.first()
        if row is None:
            raise HTTPException(status_code=404, detail="message_id not found")
        mid, answer, intent_seen, grounding_status, citations_jsonb, user_query = row

        if not user_query:
            raise HTTPException(
                status_code=400,
                detail="no preceding user message — can't construct golden case",
            )

        payload = {
            "conv_id": req.conv_id,
            "message_id": mid,
            "reason": req.reason,
            "user_query": user_query,
            "observed_answer": answer,
            "observed_intent": intent_seen,
            "observed_grounding_status": grounding_status,
            "observed_citations": citations_jsonb,
            "expected_intent": req.expected_intent,
            "ideal_refs": req.ideal_refs or [],
        }
        await s.execute(text("""
            INSERT INTO events (tenant_id, source, kind, member_id, payload)
            VALUES (current_tenant_id(), 'web', 'golden.proposal', :mid, CAST(:p AS jsonb))
        """), {
            "mid": identity.member_id,
            "p": json.dumps(payload, default=str),
        })

        # Also write a thumbs_down signal so judge / dashboards see this
        # as bad immediately (don't wait for the admin to act).
        await s.execute(text("""
            INSERT INTO message_signals (
                tenant_id, message_id, kind, value, meta, source
            ) VALUES (
                current_tenant_id(),
                CAST(:mid AS uuid),
                'thumbs_down',
                NULL,
                CAST(:meta AS jsonb),
                'web'
            )
            ON CONFLICT DO NOTHING
        """), {
            "mid": mid,
            "meta": json.dumps({"promoted_to_golden": True, "reason": req.reason[:200]}),
        })
    return None
