"""Auth router.

Local dev: /dev-token endpoint mints a JWT for any seeded member by email.
Production: /oauth-exchange takes a NextAuth-verified GitHub identity and
mints the same JWT, resolving the member by github_login (email-independent).

CLI: /oauth-device implements RFC 8628 Device Authorization Grant so the
`sediment` CLI can authenticate headless (no embedded browser server).
"""
from __future__ import annotations
import os
import secrets
import string
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import bindparam, text

from lab_lib.auth import Identity, mint_token, require_identity
from lab_lib.db import service_session
from lab_lib.settings import settings

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

    Gated by SEDIMENT_DEV_MODE=1. In production this env var is unset,
    so the endpoint 403s. Without the gate, anyone who knows a seeded
    email could mint an admin JWT (CVE-class authentication bypass).

    NextAuth.js (web) + /oauth-device/* (CLI) are the production paths.
    """
    if os.environ.get("SEDIMENT_DEV_MODE") != "1":
        raise HTTPException(status_code=403, detail="dev mode disabled")
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


# ============================================================
# RFC 8628 — Device Authorization Grant
# ============================================================
#
# Flow:
#   1. CLI:    POST /oauth-device/start
#              → { device_code, user_code, verification_uri, interval, expires_in }
#   2. CLI:    POST /oauth-device/poll  { device_code }
#              → 400 {"error":"authorization_pending"} until user confirms
#              → 400 {"error":"slow_down"} if polled faster than `interval`
#              → 400 {"error":"expired_token"} after expires_in
#              → 200 TokenResp once approved
#   3. User:   visits verification_uri in any browser, enters user_code,
#              signs in via NextAuth → web UI calls /oauth-device/approve
#              with bearer JWT + user_code.
#   4. (dev):  /oauth-device/approve-dev short-circuits #3 for tests +
#              local development. Disabled in prod via SEDIMENT_DEV_MODE.

DEVICE_CODE_TTL_S = 600        # 10 min
DEVICE_POLL_INTERVAL_S = 5
USER_CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"  # no vowels / ambiguous chars


def _gen_user_code() -> str:
    """8-char user code, dashed for readability — e.g., WDJB-MJHT."""
    chars = "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(8))
    return f"{chars[:4]}-{chars[4:]}"


def _gen_device_code() -> str:
    return secrets.token_urlsafe(32)


class DeviceStartReq(BaseModel):
    client_id: str = "sediment-cli"
    scope: str = "default"


class DeviceStartResp(BaseModel):
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


@router.post("/oauth-device/start", response_model=DeviceStartResp)
async def oauth_device_start(req: DeviceStartReq):
    """Begin a device flow. Returns codes the CLI displays + polls with."""
    device_code = _gen_device_code()
    # Loop until we hit a non-colliding user_code. Only retry on uniqueness
    # violation — other errors propagate so the caller sees the real cause.
    last_err: Exception | None = None
    for _ in range(5):
        user_code = _gen_user_code()
        try:
            async with service_session() as s:
                await s.execute(text("""
                    INSERT INTO device_authorization_codes
                      (device_code, user_code, client_id, scope, poll_interval_s, expires_at)
                    VALUES
                      (:dc, :uc, :cid, :scope, :iv,
                       now() + make_interval(secs => :ttl))
                """), {
                    "dc": device_code, "uc": user_code,
                    "cid": req.client_id, "scope": req.scope,
                    "iv": DEVICE_POLL_INTERVAL_S, "ttl": DEVICE_CODE_TTL_S,
                })
                await s.commit()
            last_err = None
            break
        except Exception as e:
            last_err = e
            # Only the unique-violation case is retryable; others should
            # surface immediately.
            if "duplicate key" not in str(e).lower() and "unique" not in str(e).lower():
                raise
            continue
    if last_err is not None:
        raise HTTPException(status_code=503, detail=f"device code generation failed: {last_err}")

    base = settings.public_base_url.rstrip("/") if hasattr(settings, "public_base_url") else ""
    verification_uri = f"{base}/device" if base else "/device"
    return DeviceStartResp(
        device_code=device_code,
        user_code=user_code,
        verification_uri=verification_uri,
        verification_uri_complete=f"{verification_uri}?user_code={user_code}",
        expires_in=DEVICE_CODE_TTL_S,
        interval=DEVICE_POLL_INTERVAL_S,
    )


class DevicePollReq(BaseModel):
    device_code: str


@router.post("/oauth-device/poll")
async def oauth_device_poll(req: DevicePollReq):
    """Poll for approval. Returns TokenResp on success, RFC 8628 error
    otherwise (always JSON body — never raises plain 4xx without body)."""
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT id::text, status, member_id::text, approved_email,
                   expires_at, poll_interval_s, last_polled_at,
                   EXTRACT(EPOCH FROM (now() - last_polled_at)) AS since_poll_s
            FROM device_authorization_codes
            WHERE device_code = :dc
        """), {"dc": req.device_code})
        row = r.first()
        if not row:
            # Don't leak existence — RFC 8628 says treat unknown as expired.
            raise HTTPException(status_code=400, detail={"error": "expired_token"})
        (did, status_, mid, approved_email, expires_at,
         interval_s, last_polled, since_poll_s) = row

        # Terminal states win over polling-cadence checks. A consumed
        # device_code (status='expired') must always look expired to the
        # caller; slow_down would mask replay attempts behind a transient
        # error message.
        if status_ in ("expired", "denied"):
            err = "access_denied" if status_ == "denied" else "expired_token"
            raise HTTPException(status_code=400, detail={"error": err})

        if expires_at <= datetime.now(timezone.utc):
            await s.execute(text(
                "UPDATE device_authorization_codes SET status='expired' WHERE id = CAST(:id AS uuid)"
            ), {"id": did})
            await s.commit()
            raise HTTPException(status_code=400, detail={"error": "expired_token"})

        if last_polled is not None and since_poll_s is not None and since_poll_s < interval_s:
            raise HTTPException(status_code=400, detail={"error": "slow_down"})

        await s.execute(text(
            "UPDATE device_authorization_codes SET last_polled_at = now() WHERE id = CAST(:id AS uuid)"
        ), {"id": did})
        await s.commit()

        if status_ != "approved":
            raise HTTPException(status_code=400, detail={"error": "authorization_pending"})

        # Atomic compare-and-swap: only the request that flips approved→expired
        # gets to mint the token. Concurrent polls land on the second SELECT
        # above seeing 'approved' but the UPDATE's WHERE filters them out so
        # their `rowcount` is 0 → treat as already-consumed.
        upd = await s.execute(text("""
            UPDATE device_authorization_codes
            SET status='expired'
            WHERE id = CAST(:id AS uuid) AND status='approved'
        """), {"id": did})
        if upd.rowcount != 1:
            await s.commit()
            raise HTTPException(status_code=400, detail={"error": "expired_token"})

        # We won the race — fetch member and mint the JWT.
        r2 = await s.execute(text("""
            SELECT id::text, tenant_id::text, role, display_name, email
            FROM members WHERE id = CAST(:id AS uuid)
        """), {"id": mid})
        m = r2.first()
        if not m:
            raise HTTPException(status_code=400, detail={"error": "access_denied"})
        member_id, tenant_id, role, display_name, email = m
        await s.commit()

    token = mint_token(member_id=member_id, tenant_id=tenant_id,
                       role=role, email=approved_email or email)
    return {
        "token": token,
        "member_id": member_id,
        "tenant_id": tenant_id,
        "role": role,
        "display_name": display_name,
    }


class DeviceApproveReq(BaseModel):
    user_code: str


@router.post("/oauth-device/approve")
async def oauth_device_approve(
    req: DeviceApproveReq,
    identity: Identity = Depends(require_identity),
):
    """Approve a pending user_code. Called by the web UI after the user
    signed in and confirmed they want to grant this device access to
    *their* identity. Binds the device row to identity.member_id."""
    uc = req.user_code.strip().upper()
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT id::text, status, expires_at
            FROM device_authorization_codes WHERE user_code = :uc
        """), {"uc": uc})
        row = r.first()
        if not row:
            raise HTTPException(status_code=404, detail="unknown user_code")
        did, status_, expires_at = row
        if status_ != "pending":
            raise HTTPException(status_code=409, detail=f"already {status_}")
        if expires_at <= datetime.now(timezone.utc):
            raise HTTPException(status_code=410, detail="expired")
        await s.execute(text("""
            UPDATE device_authorization_codes
            SET status='approved', member_id = CAST(:mid AS uuid),
                approved_email = :email, approved_at = now()
            WHERE id = CAST(:id AS uuid)
        """), {"id": did, "mid": identity.member_id, "email": identity.email})
        await s.commit()
    return {"ok": True}


class DeviceApproveDevReq(BaseModel):
    user_code: str
    email: str


@router.post("/oauth-device/approve-dev")
async def oauth_device_approve_dev(req: DeviceApproveDevReq):
    """Dev-only shortcut: approve a user_code by raw email lookup.

    Gated by SEDIMENT_DEV_MODE=1 — refuses in production. Used by tests
    and the local-dev CLI flow where there's no web UI to sign in via.
    """
    if os.environ.get("SEDIMENT_DEV_MODE") != "1":
        raise HTTPException(status_code=403, detail="dev mode disabled")
    uc = req.user_code.strip().upper()
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT id::text FROM members WHERE lower(email) = lower(:email) LIMIT 1
        """), {"email": req.email})
        m = r.first()
        if not m:
            raise HTTPException(status_code=404, detail="member not found")
        mid = m[0]
        r2 = await s.execute(text("""
            SELECT id::text, status, expires_at FROM device_authorization_codes
            WHERE user_code = :uc
        """), {"uc": uc})
        row = r2.first()
        if not row:
            raise HTTPException(status_code=404, detail="unknown user_code")
        did, status_, expires_at = row
        if status_ != "pending":
            raise HTTPException(status_code=409, detail=f"already {status_}")
        if expires_at <= datetime.now(timezone.utc):
            raise HTTPException(status_code=410, detail="expired")
        await s.execute(text("""
            UPDATE device_authorization_codes
            SET status='approved', member_id = CAST(:mid AS uuid),
                approved_email = :email, approved_at = now()
            WHERE id = CAST(:id AS uuid)
        """), {"id": did, "mid": mid, "email": req.email})
        await s.commit()
    return {"ok": True}


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
