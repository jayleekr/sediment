"""Auth router.

Local dev: /dev-token endpoint mints a JWT for any seeded member by email.
Production: /oauth-exchange takes a NextAuth-verified GitHub identity and
mints the same JWT, resolving the member by github_login (email-independent).
"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import bindparam, text

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


class OAuthExchangeReq(BaseModel):
    provider: str                       # "github"
    github_login: str | None = None
    verified_emails: list[str] = []


@router.post("/oauth-exchange", response_model=TokenResp)
async def oauth_exchange(req: OAuthExchangeReq):
    """Exchange a NextAuth-verified OAuth identity for a Sediment JWT.

    The frontend authenticates the user with GitHub (NextAuth), then calls
    this with the GitHub login + verified emails. Resolution is by
    github_login first — GitHub account emails are frequently private and
    differ from the seeded member email, so email is only a fallback for
    members not yet github-mapped. The backend remains the single source of
    truth for tenant/role and JWT signing; it never trusts a client-supplied
    tenant.
    """
    if req.provider != "github":
        raise HTTPException(status_code=400, detail=f"unsupported provider: {req.provider}")
    gh = (req.github_login or "").strip()
    emails = sorted({e.strip().lower() for e in (req.verified_emails or []) if e and e.strip()})
    if not gh and not emails:
        raise HTTPException(status_code=400, detail="github_login or verified_emails required")

    async with service_session() as s:
        row = None
        if gh:
            r = await s.execute(text("""
                SELECT m.id::text, m.tenant_id::text, m.role, m.display_name, m.email
                FROM members m
                WHERE lower(m.github_login) = lower(:gh) LIMIT 1
            """), {"gh": gh})
            row = r.first()
        if row is None and emails:
            q = text("""
                SELECT m.id::text, m.tenant_id::text, m.role, m.display_name, m.email
                FROM members m
                WHERE lower(m.email) IN :emails LIMIT 1
            """).bindparams(bindparam("emails", expanding=True))
            r = await s.execute(q, {"emails": emails})
            row = r.first()
        if row is None:
            raise HTTPException(
                status_code=403,
                detail=("GitHub account is not a Sediment member — an admin must "
                        "set your github_login in data/members.json and run `make seed`"),
            )
        mid, tid, role, name, email = row
    token = mint_token(member_id=mid, tenant_id=tid, role=role, email=email)
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
