"""JWT verification + identity extraction.

For local dev, services accept JWTs minted by sediment-platform.
The JWT carries:
  - sub: member_id
  - org_id: tenant_id
  - role: 'admin' | 'creator' | 'viewer' | 'service'

WO-7 #2 2026-05-24 — `role='service'` is a NEW server-only role for
service-to-service calls (cron jobs, internal API hops). Previously
internal callers shared the end-user JWT, which meant a leaked end-user
token had the same blast radius as the entire service surface AND a
compromised service that re-used the caller's JWT inherited admin scope.
See ``mint_service_token`` below for the constrained issuance path.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from fastapi import Header, HTTPException, status
from jose import jwt, JWTError

from .settings import settings


# Service tokens use a distinct subject prefix so revocation /audit can
# differentiate "real human acted" from "internal hop". Never matches a
# member.id (uuids; this is a plain string sentinel).
SERVICE_SUBJECT_PREFIX = "service:"


@dataclass(frozen=True)
class Identity:
    member_id: str
    tenant_id: str
    role: str = "creator"
    email: str | None = None

    @property
    def is_service(self) -> bool:
        """True if this identity was minted by ``mint_service_token``.

        Service identities have ``role='service'`` and a synthetic
        ``member_id`` starting with the ``service:`` prefix. Routes that
        accept human users only should reject these via
        ``require_human_identity``.
        """
        return self.role == "service" or self.member_id.startswith(SERVICE_SUBJECT_PREFIX)


def mint_token(member_id: str, tenant_id: str, role: str = "creator", email: str | None = None,
               ttl_seconds: int = 3600) -> str:
    import time
    if role == "service":
        # Mistake guard — service tokens must go through mint_service_token
        # so the synthetic subject prefix is preserved + caller is named.
        raise ValueError("use mint_service_token for role='service'")
    now = int(time.time())
    payload = {
        "sub": member_id,
        "org_id": tenant_id,
        "role": role,
        "email": email,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def mint_service_token(
    *,
    caller: str,
    tenant_id: str,
    ttl_seconds: int = 600,
) -> str:
    """Mint a JWT for a service-to-service call.

    WO-7 #2 2026-05-24 (REPORT.md HIGH): previously internal callers
    re-used the end-user JWT, so any internal hop inherited admin scope
    if the original caller was admin. Now: each internal caller mints its
    own bounded-scope token.

    - ``caller`` — short identifier (e.g. ``"cron.daily_ingest"``,
      ``"vault_ingester"``). Encoded as ``service:<caller>`` in ``sub`` so
      audit logs can attribute the action. Never use a member.id here.
    - ``tenant_id`` — the tenant the call acts on (RLS middleware uses this).
    - ``ttl_seconds`` — short lived (default 10 min). Internal hops are
      always sub-second; the only reason to extend is a long cron run.

    Service tokens carry ``role='service'`` — handlers that require admin
    must ``not identity.is_service and identity.role == 'admin'``.
    """
    import time
    if not caller or ":" in caller:
        raise ValueError("caller must be a non-empty identifier without ':'")
    now = int(time.time())
    payload = {
        "sub": f"{SERVICE_SUBJECT_PREFIX}{caller}",
        "org_id": tenant_id,
        "role": "service",
        "email": None,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> Identity:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"],
                             audience=settings.jwt_audience, issuer=settings.jwt_issuer)
    except JWTError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"invalid token: {e}")
    return Identity(
        member_id=payload["sub"],
        tenant_id=payload["org_id"],
        role=payload.get("role", "creator"),
        email=payload.get("email"),
    )


async def require_identity(authorization: str = Header(default="")) -> Identity:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    identity = decode_token(token)
    await _ensure_not_revoked(identity)
    return identity


# ---- revocation check (members.revoked_at kill-switch) ----
#
# JWTs are stateless — to revoke before exp we check the DB. Hot path,
# so the lookup is cached in-process with a 30s TTL. Trade-off: a revoked
# member can keep calling for ≤30s after revocation. Acceptable for the
# kill-switch's purpose (incident response on the order of minutes).
import time as _time

_REVOKED_CACHE: dict[str, tuple[float, bool]] = {}  # member_id → (expires_at, revoked)
_REVOKED_TTL = 30.0


async def _ensure_not_revoked(identity: Identity) -> None:
    # Service tokens (WO-7 #2) have synthetic subjects with no members row;
    # the revocation kill-switch doesn't apply. Revoking a service caller
    # means rotating the JWT_SECRET, which invalidates EVERY token (the
    # nuclear option, intentionally distinct from member kill-switch).
    if identity.is_service:
        return
    now = _time.monotonic()
    cached = _REVOKED_CACHE.get(identity.member_id)
    if cached and cached[0] > now:
        if cached[1]:
            raise HTTPException(status_code=401, detail="member revoked")
        return
    # Cache miss — query DB. Import inside the function to avoid an import
    # cycle (lab_lib.db imports lab_lib.settings imports lab_lib.auth).
    from .db import service_session  # type: ignore
    from sqlalchemy import text
    async with service_session() as s:
        r = await s.execute(text(
            "SELECT revoked_at IS NOT NULL FROM members WHERE id = :id"
        ), {"id": identity.member_id})
        row = r.first()
    revoked = bool(row and row[0])
    _REVOKED_CACHE[identity.member_id] = (now + _REVOKED_TTL, revoked)
    if revoked:
        raise HTTPException(status_code=401, detail="member revoked")


async def require_human_identity(authorization: str = Header(default="")) -> Identity:
    """Like ``require_identity`` but rejects service tokens.

    Routes that should be hit only by an actual human end-user (UI account
    settings, /profile, billing portal flows) should use this dependency
    instead of the generic ``require_identity``. WO-7 #2 — service tokens
    can hit ``require_identity`` for internal hops without elevating to
    human-only surfaces.
    """
    identity = await require_identity(authorization)
    if identity.is_service:
        raise HTTPException(status_code=403, detail="service token cannot access this endpoint")
    return identity


async def optional_identity(authorization: str = Header(default="")) -> Optional[Identity]:
    if not authorization.startswith("Bearer "):
        return None
    try:
        return decode_token(authorization.removeprefix("Bearer ").strip())
    except HTTPException:
        return None
