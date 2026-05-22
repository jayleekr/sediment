"""FastAPI middleware that attaches tenant context to each request.

Three concerns, kept here because they share the JWT-decode pass:
  1. Decode JWT → request.state.identity (existing)
  2. Per-member rate limit (token bucket; 429 + Retry-After)
  3. Audit log to mcp_call_log (fire-and-forget)
"""
from __future__ import annotations
import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from .auth import decode_token
from .audit import audit_log
from .rate_limit import default_limiter


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Decode JWT → attach Identity to request.state. Use in every service."""

    PUBLIC_PATHS = {"/healthz", "/readyz", "/openapi.json", "/docs", "/redoc"}
    # Auth endpoints must be public (you can't have a token before logging in).
    PUBLIC_PREFIXES = (
        "/docs", "/static", "/api/v1/auth/", "/api/v1/billing/webhook",
        # /api/v1/issuer/* uses HTTP Basic (per-instructor passphrase), not JWT
        # (sediment#14). Router handles its own auth; let it through.
        "/api/v1/issuer/",
    )
    # Routes for which we audit + rate-limit. (Auth/billing/health already
    # excluded by the PUBLIC_* filters above.)
    AUDIT_PREFIXES = ("/api/v1/library", "/api/v1/conversations", "/api/v1/members",
                      "/api/v1/vault", "/api/v1/costs", "/api/v1/feedback")

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method == "OPTIONS":
            return await call_next(request)

        # NUL byte (0x00) is not valid in Postgres TEXT columns and any query
        # param carrying it would crash a downstream SQL bind with
        # CharacterNotInRepertoireError (sediment#18). Strip NULs from query
        # params before they reach any handler. Cheap O(N) check.
        if "%00" in (request.url.query or "") or "\x00" in str(request.url):
            return JSONResponse(
                {"error": "request contains NUL byte"}, status_code=400
            )

        path = request.url.path
        if path in self.PUBLIC_PATHS or any(path.startswith(p) for p in self.PUBLIC_PREFIXES):
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse({"error": "missing bearer token"}, status_code=401)
        try:
            identity = decode_token(auth.removeprefix("Bearer ").strip())
        except Exception as e:
            return JSONResponse({"error": f"invalid token: {e}"}, status_code=401)

        request.state.identity = identity
        request.state.tenant_id = identity.tenant_id

        # ---- rate limit (per-member, in-process token bucket) ----
        rl = default_limiter()
        allowed, retry_after = await rl.check(identity.member_id)
        if not allowed:
            return JSONResponse(
                {"error": "rate_limited", "retry_after_s": round(retry_after, 1)},
                status_code=429,
                headers={"Retry-After": str(max(1, int(retry_after) + 1))},
            )

        # ---- request execution + audit ----
        t0 = time.monotonic()
        response = await call_next(request)
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        # Audit only the routes that matter for cost/usage tracking.
        if any(path.startswith(p) for p in self.AUDIT_PREFIXES):
            # Try to estimate result_bytes from Content-Length header (cheap;
            # no need to buffer the body). Bodies streamed via SSE won't have
            # a header — record 0 in that case.
            try:
                rb = int(response.headers.get("content-length", "0"))
            except (TypeError, ValueError):
                rb = 0
            client = request.headers.get("x-sediment-client") or "web"
            error_reason = None
            if response.status_code >= 400:
                error_reason = f"http_{response.status_code}"
            tool = path.split("?")[0]
            audit_log(
                identity.tenant_id, identity.member_id, tool,
                latency_ms=elapsed_ms,
                client=client,
                result_bytes=rb,
                status_code=response.status_code,
                error_reason=error_reason,
            )

        return response
