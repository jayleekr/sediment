"""Curator Platform :10100

Main REST API. Tenant-aware (RLS) for all read/write.
Some endpoints (auth/login, signup) operate without tenant context and use
service role for explicit writes.
"""
from __future__ import annotations
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from lab_lib.logging import configure_logging, get_logger
from lab_lib.tenant_middleware import TenantContextMiddleware

from .routers import (
    auth, conversations, library, members, ingest_proxy,
    feedback, costs, admin, onboarding, billing, cite_export, vault,
    issuer, signals, promote_to_golden,
)

configure_logging()
log = get_logger("platform")

app = FastAPI(title="Curator Platform", version="0.1.0")

app.add_middleware(TenantContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    # Frontend deploys to Vercel (preview = *.vercel.app, prod = the custom
    # domain) so the exact origin isn't known until deploy. Regex covers
    # local dev + every Vercel deploy + hypeproof domains. allow_credentials
    # forbids "*", hence the regex form.
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_origin_regex=r"https://([a-z0-9-]+\.)?(vercel\.app|hypeproof-ai\.xyz|hypeproof\.studio)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "service": "curator-platform"}


# Routers
app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(conversations.router, prefix="/api/v1/conversations", tags=["conversations"])
app.include_router(library.router, prefix="/api/v1/library", tags=["library"])
app.include_router(members.router, prefix="/api/v1/members", tags=["members"])
app.include_router(ingest_proxy.router, prefix="/api/v1/ingest", tags=["ingest"])
app.include_router(feedback.router, prefix="/api/v1/feedback", tags=["feedback"])
app.include_router(promote_to_golden.router, prefix="/api/v1/feedback")
app.include_router(cite_export.router, prefix="/api/v1/events/cite-export", tags=["activation"])
app.include_router(vault.router, prefix="/api/v1/vault", tags=["vault"])
app.include_router(costs.router, prefix="/api/v1/costs", tags=["costs"])
# sediment#15 Phase 2: implicit/explicit signal writer. Router already
# carries its own /api/v1/signals prefix.
app.include_router(signals.router)
app.include_router(admin.router, prefix="/api/v1/admin", tags=["admin"])
app.include_router(onboarding.router, prefix="/api/v1/onboard", tags=["onboarding"])
app.include_router(billing.router, prefix="/api/v1/billing", tags=["billing"])
# HypeProof Studio instructor issuer-token self-service (sediment/#14).
app.include_router(issuer.router, prefix="/api/v1/issuer", tags=["issuer"])
