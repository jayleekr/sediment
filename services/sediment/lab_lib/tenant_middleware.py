"""FastAPI middleware that attaches tenant context to each request."""
from __future__ import annotations
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response
from .auth import decode_token


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Decode JWT → attach Identity to request.state. Use in every service."""

    PUBLIC_PATHS = {"/healthz", "/readyz", "/openapi.json", "/docs", "/redoc"}
    # Auth endpoints must be public (you can't have a token before logging in).
    PUBLIC_PREFIXES = ("/docs", "/static", "/api/v1/auth/", "/api/v1/billing/webhook")

    async def dispatch(self, request: Request, call_next) -> Response:
        # CORS preflight (OPTIONS) must pass through untouched so the
        # downstream CORSMiddleware can add Access-Control-* headers. Without
        # this, browsers see "blocked by CORS policy" and the /api/v1/* surface
        # is unreachable from the Next.js frontend.
        if request.method == "OPTIONS":
            return await call_next(request)

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
        return await call_next(request)
