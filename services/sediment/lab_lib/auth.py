"""JWT verification + identity extraction.

For local dev, services accept JWTs minted by curator-platform.
The JWT carries:
  - sub: member_id
  - org_id: tenant_id
  - role: 'admin' | 'creator' | 'viewer'
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from fastapi import Header, HTTPException, status
from jose import jwt, JWTError

from .settings import settings


@dataclass(frozen=True)
class Identity:
    member_id: str
    tenant_id: str
    role: str = "creator"
    email: str | None = None


def mint_token(member_id: str, tenant_id: str, role: str = "creator", email: str | None = None,
               ttl_seconds: int = 3600) -> str:
    import time
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


async def optional_identity(authorization: str = Header(default="")) -> Optional[Identity]:
    if not authorization.startswith("Bearer "):
        return None
    try:
        return decode_token(authorization.removeprefix("Bearer ").strip())
    except HTTPException:
        return None
