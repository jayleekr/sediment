"""Auth router.

Local dev: /dev-token endpoint mints a JWT for any seeded member by email.
Production: /callback handles NextAuth.js + Discord OAuth (Phase 5).
"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from lab_lib.auth import Identity, mint_token, require_identity
from lab_lib.db import service_session

router = APIRouter()


class DevTokenReq(BaseModel):
    email: str


class TokenResp(BaseModel):
    token: str
    member_id: str
    tenant_id: str
    role: str
    display_name: str


@router.post("/dev-token", response_model=TokenResp)
async def dev_token(req: DevTokenReq):
    """Local dev only — mint a JWT for any seeded member.

    NextAuth.js (Phase 5) replaces this with proper magic-link / OAuth.
    """
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT m.id::text, m.tenant_id::text, m.role, m.display_name
            FROM members m WHERE m.email = :email LIMIT 1
        """), {"email": req.email})
        row = r.first()
        if not row:
            raise HTTPException(status_code=404, detail="member not found — run `make seed`")
        mid, tid, role, name = row
    token = mint_token(member_id=mid, tenant_id=tid, role=role, email=req.email)
    return TokenResp(token=token, member_id=mid, tenant_id=tid, role=role, display_name=name)


class WhoamiResp(BaseModel):
    member_id: str
    tenant_id: str
    role: str
    email: str
    display_name: str


@router.get("/whoami", response_model=WhoamiResp)
async def whoami(identity: Identity = Depends(require_identity)):
    """Return the authenticated identity. Used by MCP clients to verify the
    token works before issuing real queries. Also confirms the tenant
    binding so callers know which vault they're seeing.
    """
    # Identity has member_id + tenant_id + role + email. display_name is in DB.
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT display_name FROM members WHERE id = :mid LIMIT 1
        """), {"mid": identity.member_id})
        row = r.first()
    display_name = row[0] if row else "(unknown)"
    return WhoamiResp(
        member_id=identity.member_id, tenant_id=identity.tenant_id,
        role=identity.role, email=identity.email or "",
        display_name=display_name,
    )
