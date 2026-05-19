"""cite-into-work event — the Activation Engine's magic-number signal.

Logged when a member moves a cited answer OUT of Sediment into their work:
copy to clipboard, @teammate hand-off, or thread-share. `clipboard` is a
PROXY for artifact reuse (we cannot trace what happens after the copy);
`@teammate` / `thread-share` are fully-observed hand-offs and also drive S5.
See ACTIVATION_ENGINE.md §3 / §5.
"""
from __future__ import annotations
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from lab_lib.auth import Identity, require_identity
from lab_lib.db import app_session

router = APIRouter()

CITE_EXPORT_TARGETS = {"clipboard", "@teammate", "thread-share"}


class CiteExportReq(BaseModel):
    target: str                       # one of CITE_EXPORT_TARGETS
    message_id: str | None = None
    citation_ref: str | None = None   # the artifact ref the citation points at


@router.post("")
async def post_cite_export(
    req: CiteExportReq, identity: Identity = Depends(require_identity)
):
    if req.target not in CITE_EXPORT_TARGETS:
        raise HTTPException(
            status_code=400,
            detail=f"target must be one of {sorted(CITE_EXPORT_TARGETS)}",
        )
    async with app_session(identity.tenant_id) as s:
        await s.execute(text("""
            INSERT INTO events (tenant_id, source, kind, member_id, payload)
            VALUES (current_tenant_id(), 'web', 'cite_export', :mid, CAST(:p AS jsonb))
        """), {
            "mid": identity.member_id,
            "p": json.dumps({
                "target": req.target,
                "message_id": req.message_id,
                "citation_ref": req.citation_ref,
            }),
        })
    return {"ok": True}
