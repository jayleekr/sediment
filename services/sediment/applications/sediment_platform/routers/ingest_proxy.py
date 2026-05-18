"""Ingest proxy — forwards to vault-ingester (system role)."""
from __future__ import annotations
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from lab_lib.auth import Identity, require_identity
from lab_lib.settings import settings

router = APIRouter()


class IngestDocReq(BaseModel):
    ref: str
    type: str
    body: str


@router.post("/document")
async def ingest_document(req: IngestDocReq, identity: Identity = Depends(require_identity)):
    """Member-initiated ingest of a single document. Tenant from JWT."""
    if identity.role not in ("admin", "creator"):
        raise HTTPException(status_code=403, detail="insufficient role")
    payload = {
        "tenant_id": identity.tenant_id,
        "ref": req.ref,
        "type": req.type,
        "body": req.body,
        "author_external_id": identity.member_id,
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"http://localhost:{settings.vault_ingester_port}/v1/ingest/document",
            json=payload, timeout=120,
        )
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"ingester error: {r.text[:200]}")
        return r.json()
