"""HypeProof Studio instructor issuer-token endpoint (sediment/#14).

Serves the per-instructor issuer-role tokens that workshop 강사 use to mint
student tokens via the Cloudflare worker's /admin/tokens/issue endpoint.

Replaces the prior "Jay holds /tmp/issuer-tokens/*.json + DMs on request"
pattern with a self-service flow:

- `GET  /api/v1/issuer/{name}` — return current token (per-instructor Basic)
- `POST /api/v1/issuer/{name}/rotate` — mint new token + revoke old + audit

Auth model: HTTP Basic. Username = instructor name ("tj", "bongho", ...),
password = `INSTRUCTOR_<NAME>_PASSPHRASE` env var (set on Fly per-instructor).

Token storage: `INSTRUCTOR_<NAME>_TOKEN` env var. Initialized from
/tmp/issuer-tokens/*.json snapshot; updated in-process on rotate (best-effort
— if the Fly instance restarts before secrets-update, the GET will still
return the LAST FLY-SET value; the new token from rotate is also returned
to caller, so they can save it independently).

Crypto is a Python port of `worker/src/lib/tokens.ts` `issueIssuer`:
HMAC-SHA256 over a canonicalized JSON payload (specific field order),
base64url-encoded (no padding). Same `HPS_SIGNING_SECRET` Fly secret
verifies in the Cloudflare worker — round-trip tested in tests/.

Rate limit: simple in-memory dict (Fly single instance). Buckets per
(instructor, IP). Limits GET 10/h, POST 3/d.

Audit: every request logs to structlog. Rotate additionally posts to
Discord webhook (`HYPEPROOF_AUDIT_DISCORD_WEBHOOK`) so a leak / unauthorized
rotate is visible immediately.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets as secrets_module
import time
import uuid
from collections import defaultdict, deque
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from lab_lib.logging import get_logger

router = APIRouter()
log = get_logger("issuer")
basic = HTTPBasic(auto_error=False)


# ─── crypto: Python port of worker/src/lib/tokens.ts issueIssuer ─────────
# Verified byte-for-byte against the TS sign+verify path; round-trip tested.

def _b64u_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _canonicalize(payload: dict[str, Any]) -> bytes:
    """Match `canonicalize` in worker/src/lib/tokens.ts EXACTLY.

    Field order: c, exp, iat, p, u, v, then optional jti, role, scopes
    appended only if present. JSON.stringify default (no whitespace).
    """
    out: dict[str, Any] = {
        "c": payload["c"],
        "exp": payload["exp"],
        "iat": payload["iat"],
        "p": payload["p"],
        "u": payload["u"],
        "v": payload["v"],
    }
    if "jti" in payload:
        out["jti"] = payload["jti"]
    if "role" in payload:
        out["role"] = payload["role"]
    if "scopes" in payload:
        out["scopes"] = payload["scopes"]
    return json.dumps(out, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def mint_issuer_token(
    instructor: str,
    scopes: list[dict[str, Any]],
    hours: int,
    secret: str,
) -> dict[str, Any]:
    """Issue a fresh issuer-role token. Mirrors `issueIssuer` in TS."""
    if not secret or len(secret) < 16:
        raise ValueError("signing secret too short")
    if not scopes:
        raise ValueError("issuer must have at least one scope")

    now = int(time.time())
    jti = str(uuid.uuid4())
    payload = {
        "u": instructor,
        "c": "__issuer__",
        "p": "__issuer__",
        "role": "issuer",
        "scopes": scopes,
        "v": 2,
        "iat": now,
        "exp": now + hours * 3600,
        "jti": jti,
    }
    payload_bytes = _canonicalize(payload)
    sig = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).digest()
    token = f"{_b64u_encode(payload_bytes)}.{_b64u_encode(sig)}"
    return {"token": token, "jti": jti, "exp": payload["exp"]}


def decode_token_payload(token: str) -> dict[str, Any]:
    """Decode (without verifying) — used for surfacing jti/exp/scopes."""
    parts = token.split(".", 1)
    payload_bytes = _b64u_decode(parts[0])
    return json.loads(payload_bytes.decode("utf-8"))


# ─── registry: env-var-backed instructor records ─────────────────────────


def _env_key(prefix: str, name: str) -> str:
    return f"INSTRUCTOR_{name.upper()}_{prefix}"


def _get_instructor_token(name: str) -> str | None:
    return os.environ.get(_env_key("TOKEN", name))


def _set_instructor_token(name: str, token: str) -> None:
    """In-process update only — Fly secrets are not mutated automatically.
    If the instance restarts, the value reverts to the last `fly secrets set`.
    Caller receives the new token in the response so they can save it.
    """
    os.environ[_env_key("TOKEN", name)] = token


def _get_instructor_passphrase(name: str) -> str | None:
    return os.environ.get(_env_key("PASSPHRASE", name))


# ─── rate limit: in-memory leaky bucket ──────────────────────────────────

_GET_BUDGET = (10, 3600)   # 10 reads per hour
_POST_BUDGET = (3, 86400)  # 3 rotates per day
_BUCKETS: dict[str, deque[float]] = defaultdict(deque)


def _check_rate(key: str, limit: int, window: int) -> tuple[bool, int]:
    """Returns (allowed, retry_after_seconds_if_blocked)."""
    bucket = _BUCKETS[key]
    now = time.time()
    cutoff = now - window
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= limit:
        retry = int(bucket[0] + window - now) + 1
        return False, retry
    bucket.append(now)
    return True, 0


# ─── audit: structlog + optional Discord webhook ─────────────────────────


async def _post_discord(event: str, payload: dict[str, Any]) -> None:
    url = os.environ.get("HYPEPROOF_AUDIT_DISCORD_WEBHOOK")
    if not url:
        return
    body = {
        "username": "sediment/issuer",
        "embeds": [
            {
                "title": event,
                "color": 0xCB6E17 if event.startswith("rotate") else 0x5B6B73,
                "fields": [{"name": k, "value": str(v)[:1000], "inline": True} for k, v in payload.items()],
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(url, json=body)
    except Exception as e:  # noqa: BLE001 — audit must never break the request
        log.warning("audit.discord.post_failed", err=str(e), event=event)


# ─── auth: per-instructor HTTP Basic ─────────────────────────────────────


def _authenticate(name: str, creds: HTTPBasicCredentials | None) -> None:
    if creds is None:
        raise HTTPException(status_code=401, detail="Basic auth required", headers={"WWW-Authenticate": 'Basic realm="instructor"'})
    expected = _get_instructor_passphrase(name)
    # constant-time username + passphrase comparison; reject unknown instructors
    # via the same code path so an attacker can't enumerate names.
    user_ok = secrets_module.compare_digest(creds.username.encode("utf-8"), name.encode("utf-8"))
    pass_ok = expected is not None and secrets_module.compare_digest(
        creds.password.encode("utf-8"), expected.encode("utf-8")
    )
    if not (user_ok and pass_ok):
        raise HTTPException(status_code=401, detail="bad credentials", headers={"WWW-Authenticate": 'Basic realm="instructor"'})


# ─── response models ─────────────────────────────────────────────────────


class IssuerTokenResp(BaseModel):
    instructor: str
    token: str
    jti: str
    exp: int            # unix seconds
    scopes: list[dict[str, Any]]


class RotateReq(BaseModel):
    confirm: bool = False
    days: int = 60
    max_hours: int = 12
    can_start_session: bool = False
    max_session_hours: int = 4


class HealthResp(BaseModel):
    ok: bool
    service: str = "issuer"
    instructors_provisioned: int


# ─── endpoints ───────────────────────────────────────────────────────────


@router.get("/_health", response_model=HealthResp)
async def health() -> HealthResp:
    """No-auth liveness + count of provisioned instructors."""
    count = 0
    for k in os.environ:
        if k.startswith("INSTRUCTOR_") and k.endswith("_TOKEN"):
            count += 1
    return HealthResp(ok=True, instructors_provisioned=count)


@router.get("/{name}", response_model=IssuerTokenResp)
async def get_token(
    name: str,
    request: Request,
    creds: HTTPBasicCredentials | None = Depends(basic),
) -> IssuerTokenResp:
    """Return the instructor's current issuer token."""
    name = name.lower().strip()
    _authenticate(name, creds)

    # Rate limit per (instructor, IP)
    rate_key = f"GET:{name}:{request.client.host if request.client else 'unknown'}"
    allowed, retry = _check_rate(rate_key, *_GET_BUDGET)
    if not allowed:
        raise HTTPException(429, detail=f"rate limited; retry in {retry}s", headers={"Retry-After": str(retry)})

    token = _get_instructor_token(name)
    if not token:
        raise HTTPException(404, detail=f"no token provisioned for {name}")

    payload = decode_token_payload(token)
    log.info("issuer.get", instructor=name, jti=payload.get("jti"), ip=request.client.host if request.client else None)

    return IssuerTokenResp(
        instructor=name,
        token=token,
        jti=payload.get("jti", ""),
        exp=int(payload.get("exp", 0)),
        scopes=payload.get("scopes", []),
    )


@router.post("/{name}/rotate", response_model=IssuerTokenResp)
async def rotate_token(
    name: str,
    req: RotateReq,
    request: Request,
    creds: HTTPBasicCredentials | None = Depends(basic),
) -> IssuerTokenResp:
    """Mint a new issuer token. Old jti is revoked via the Cloudflare worker
    admin endpoint (best-effort — revoke failure is logged, not fatal).

    Requires `confirm: true` in the body to prevent accidental rotation.
    """
    name = name.lower().strip()
    _authenticate(name, creds)

    if not req.confirm:
        raise HTTPException(400, detail="set confirm:true in body to acknowledge rotation")
    if req.can_start_session and not (1 <= req.max_session_hours <= 24):
        raise HTTPException(400, detail="max_session_hours must be 1..24 when can_start_session=true")

    rate_key = f"POST:{name}"
    allowed, retry = _check_rate(rate_key, *_POST_BUDGET)
    if not allowed:
        raise HTTPException(429, detail=f"rate limited; retry in {retry}s", headers={"Retry-After": str(retry)})

    signing_secret = os.environ.get("HPS_SIGNING_SECRET")
    if not signing_secret:
        raise HTTPException(503, detail="HPS_SIGNING_SECRET not configured on this sediment instance")

    # Scope mirror — same scope template the 5/20 batch used.
    session_extras = (
        {"can_start_session": True, "max_session_hours": req.max_session_hours}
        if req.can_start_session
        else {}
    )
    scopes = [
        {
            "cohort": "boah-dental-2026-a",
            "profiles": ["boah-dental-teaser-2026-s1", "sk-biopharm-kids-2026-grade-3-4-s1"],
            "max_hours": req.max_hours,
            **session_extras,
        },
        {
            "cohort": "sk-biopharm-2026-a",
            "profiles": ["boah-dental-teaser-2026-s1", "sk-biopharm-kids-2026-grade-3-4-s1"],
            "max_hours": req.max_hours,
            **session_extras,
        },
    ]
    hours = req.days * 24
    minted = mint_issuer_token(name, scopes, hours, signing_secret)

    # Best-effort: revoke old jti via worker admin
    old_token = _get_instructor_token(name)
    old_jti = None
    if old_token:
        try:
            old_jti = decode_token_payload(old_token).get("jti")
        except Exception:
            pass
    revoke_status = "skipped"
    if old_jti:
        admin_pw = os.environ.get("HPS_ADMIN_PASSWORD")
        worker_url = os.environ.get("HPS_WORKER_URL", "https://api.hypeproof-ai.xyz")
        if admin_pw:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    auth = base64.b64encode(f":{admin_pw}".encode("utf-8")).decode("ascii")
                    r = await client.post(
                        f"{worker_url}/admin/tokens/revoke",
                        headers={"Authorization": f"Basic {auth}", "content-type": "application/json"},
                        json={"jti": old_jti, "reason": f"sediment rotate: instructor {name} self-rotated"},
                    )
                    revoke_status = f"http_{r.status_code}"
            except Exception as e:
                revoke_status = f"err_{type(e).__name__}"

    # Update in-process env (best-effort persistence — see module docstring)
    _set_instructor_token(name, minted["token"])

    log.warning(
        "issuer.rotate",
        instructor=name,
        new_jti=minted["jti"],
        old_jti=old_jti,
        revoke=revoke_status,
        ip=request.client.host if request.client else None,
    )
    await _post_discord(
        "rotate",
        {
            "instructor": name,
            "new_jti": minted["jti"],
            "old_jti": old_jti or "(none)",
            "revoke": revoke_status,
            "ip": request.client.host if request.client else "?",
        },
    )

    return IssuerTokenResp(
        instructor=name,
        token=minted["token"],
        jti=minted["jti"],
        exp=minted["exp"],
        scopes=scopes,
    )
