"""Tenant onboarding — Phase 6 stub.

Creates a new tenant + initial owner member + default subscription.
Authenticated via service token (NextAuth callback after sign-up flow).
"""
from __future__ import annotations
import secrets as _secrets
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import text

from lab_lib.auth import mint_token
from lab_lib.db import service_session
from lab_lib.settings import settings

router = APIRouter()


class OnboardReq(BaseModel):
    workspace_name: str
    workspace_slug: str
    owner_email: EmailStr
    owner_display_name: str
    invite_emails: list[EmailStr] = []
    plan: str = "free"


class OnboardResp(BaseModel):
    tenant_id: str
    tenant_slug: str
    owner_member_id: str
    owner_token: str


def _check_service_key(x_service_key: str | None):
    """Verify the request bears the shared onboarding secret.

    2026-05-23 FIX-B — previously this only checked the header was non-empty,
    which made `/api/v1/onboard` an open admin-JWT minting service. Two
    reviewers independently flagged it CRITICAL (output/reviews/.../REPORT.md
    #4). Now:
      - prod (secret set, non-empty): constant-time compare via secrets.compare_digest
      - dev (secret empty AND header empty): accept (legacy local behavior)
      - dev (secret empty BUT header present): accept the header verbatim
        — keeps existing dev scripts that send any string working, but the
        explicit empty-secret dev mode is the only path that doesn't compare.
    Production path is locked by validate_runtime_secrets() at app boot —
    if SEDIMENT_ONBOARDING_SECRET is empty in prod, the process won't start.
    """
    expected = settings.sediment_onboarding_secret
    if not expected:
        # Dev path — no secret configured. Still require *something* to keep
        # the route from being trivially scriptable by accident.
        if not x_service_key:
            raise HTTPException(status_code=401, detail="missing X-Service-Key")
        return
    # Prod path — constant-time compare. compare_digest needs bytes/str of
    # equal length to avoid leaking length; both args are str so equal-length
    # branch isn't required here.
    if not x_service_key or not _secrets.compare_digest(x_service_key, expected):
        raise HTTPException(status_code=401, detail="invalid X-Service-Key")


@router.post("", response_model=OnboardResp)
async def onboard(req: OnboardReq, x_service_key: str | None = Header(default=None)):
    _check_service_key(x_service_key)

    async with service_session() as s:
        # Tenant
        r = await s.execute(text("""
            INSERT INTO tenants (slug, display_name, plan, status, region, domain)
            VALUES (:slug, :name, :plan, 'trialing', 'us', :domain)
            RETURNING id::text
        """), {
            "slug": req.workspace_slug,
            "name": req.workspace_name,
            "plan": req.plan,
            "domain": f"{req.workspace_slug}.curator.hypeproof-ai.xyz",
        })
        tid = r.scalar_one()

        # Subscription (free default)
        seat_default = {"free": 3, "pro": 5, "business": 10}.get(req.plan, 3)
        quota_default = {"free": 1000, "pro": 2500, "business": 15000}.get(req.plan, 1000)
        await s.execute(text("""
            INSERT INTO subscriptions (tenant_id, seat_count, query_quota_per_month, storage_quota_gb)
            VALUES (:tid, :seats, :quota, 5)
        """), {"tid": tid, "seats": seat_default, "quota": quota_default})

        # Owner member
        r2 = await s.execute(text("""
            INSERT INTO members (tenant_id, email, display_name, role, title)
            VALUES (:tid, :email, :name, 'admin', 'Owner')
            RETURNING id::text
        """), {"tid": tid, "email": req.owner_email, "name": req.owner_display_name})
        owner_id = r2.scalar_one()

        # Pending invites — store as events
        for inv in req.invite_emails:
            await s.execute(text("""
                INSERT INTO events (tenant_id, source, kind, payload)
                VALUES (:tid, 'web', 'invite.pending', CAST(:p AS jsonb))
            """), {"tid": tid, "p": _json({"email": inv, "by": req.owner_email})})

        # Audit
        await s.execute(text("""
            INSERT INTO audit_log (tenant_id, actor_id, action, resource, payload)
            VALUES (:tid, :mid, 'tenant.created', 'tenants/'||CAST(:tid AS text), CAST(:p AS jsonb))
        """), {"tid": tid, "mid": owner_id, "p": _json({"plan": req.plan})})

    token = mint_token(member_id=owner_id, tenant_id=tid, role="admin", email=req.owner_email)
    return OnboardResp(
        tenant_id=tid, tenant_slug=req.workspace_slug,
        owner_member_id=owner_id, owner_token=token,
    )


def _json(d):
    import json
    return json.dumps(d, default=str)
