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
    return decode_token(token)


async def optional_identity(authorization: str = Header(default="")) -> Optional[Identity]:
    if not authorization.startswith("Bearer "):
        return None
    try:
        return decode_token(authorization.removeprefix("Bearer ").strip())
    except HTTPException:
        return None
