"""Admin endpoints — tenant management, audit log."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from lab_lib.auth import Identity, require_identity
from lab_lib.db import app_session, service_session

router = APIRouter()


def _require_admin(identity: Identity):
    if identity.role != "admin":
        raise HTTPException(status_code=403, detail="admin only")


@router.get("/tenants")
async def list_tenants(identity: Identity = Depends(require_identity)):
    """List ALL tenants — superadmin only (uses service role).

    For Phase 6+ when there are multiple tenants. For MVP, only HypeProof admin sees this.
    """
    _require_admin(identity)
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT t.id::text, t.slug, t.display_name, t.plan, t.status, t.region,
                   t.created_at, sub.seat_count, sub.query_quota_per_month,
                   (SELECT count(*) FROM members m WHERE m.tenant_id = t.id) AS member_count,
                   (SELECT count(*) FROM artifacts a WHERE a.tenant_id = t.id) AS artifact_count
            FROM tenants t LEFT JOIN subscriptions sub ON sub.tenant_id = t.id
            ORDER BY t.created_at
        """))
        return {"items": [dict(row._mapping) for row in r]}


@router.get("/audit")
async def audit_log(identity: Identity = Depends(require_identity), limit: int = 100):
    _require_admin(identity)
    async with app_session(identity.tenant_id) as s:
        r = await s.execute(text("""
            SELECT id::text, actor_id::text, action, resource, payload, ts
            FROM audit_log ORDER BY ts DESC LIMIT :limit
        """), {"limit": limit})
        return {"items": [dict(row._mapping) for row in r]}
