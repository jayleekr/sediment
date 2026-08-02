"""Admin endpoints — tenant management, audit log, retrieval aliases."""
from __future__ import annotations
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from lab_lib.aliases import invalidate_cache
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


# ============================================================
# Retrieval aliases (sediment#139)
# ============================================================
# These rows are what used to be hardcoded keyword maps in search_utils.py and
# library.py. Exposing CRUD here is the point of the migration: a tenant's
# retrieval vocabulary must be changeable without a code change and a deploy.

AliasKind = Literal["type", "ref_prefix", "entity", "demote_ref_prefix"]


class AliasUpsertReq(BaseModel):
    alias: str = Field(..., min_length=1, max_length=200,
                       description="Query token to match. For demote_ref_prefix, "
                                   "pass the ref prefix itself — there is no query "
                                   "token, the row IS the rule.")
    target_kind: AliasKind
    target_value: str = Field(..., min_length=1, max_length=400)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0,
                              description="Breaks ties between aliases; higher wins.")


@router.get("/aliases")
async def list_aliases(identity: Identity = Depends(require_identity)):
    """This tenant's retrieval vocabulary. RLS scopes the read."""
    _require_admin(identity)
    async with app_session(identity.tenant_id) as s:
        r = await s.execute(text("""
            SELECT id::text, alias, target_kind, target_value, source, confidence, created_at
            FROM tenant_aliases
            ORDER BY target_kind, confidence DESC, alias
        """))
        return {"items": [dict(row._mapping) for row in r]}


@router.put("/aliases")
async def upsert_alias(req: AliasUpsertReq,
                       identity: Identity = Depends(require_identity)):
    """Add or update one alias. source is forced to 'manual' — 'seed' means
    machine-migrated from the old hardcoded maps and 'learned' is reserved for
    automatic extraction, so neither may be claimed by a human edit."""
    _require_admin(identity)
    async with app_session(identity.tenant_id) as s:
        r = await s.execute(text("""
            INSERT INTO tenant_aliases
              (tenant_id, alias, target_kind, target_value, source, confidence)
            VALUES (CAST(:tid AS uuid), :alias, :kind, :target, 'manual', :conf)
            ON CONFLICT (tenant_id, alias, target_kind) DO UPDATE SET
              target_value = EXCLUDED.target_value,
              confidence = EXCLUDED.confidence,
              source = 'manual'
            RETURNING id::text
        """), {
            "tid": str(identity.tenant_id), "alias": req.alias.lower(),
            "kind": req.target_kind, "target": req.target_value,
            "conf": req.confidence,
        })
        alias_id = r.scalar_one()
        await s.execute(text("""
            INSERT INTO audit_log (tenant_id, actor_id, action, resource, payload)
            VALUES (CAST(:tid AS uuid), CAST(:actor AS uuid), 'alias.upsert', :res,
                    CAST(:p AS jsonb))
        """), {
            "tid": str(identity.tenant_id), "actor": identity.member_id,
            "res": f"tenant_aliases/{alias_id}",
            "p": req.model_dump_json(),
        })
    # The retrieval path caches this per tenant for CACHE_TTL_SECONDS; drop it
    # so an admin sees their edit take effect immediately rather than "in about
    # a minute", which reads as the edit not having worked.
    invalidate_cache(str(identity.tenant_id))
    return {"id": alias_id}


@router.delete("/aliases/{alias_id}")
async def delete_alias(alias_id: str,
                       identity: Identity = Depends(require_identity)):
    _require_admin(identity)
    async with app_session(identity.tenant_id) as s:
        r = await s.execute(
            text("DELETE FROM tenant_aliases WHERE id = CAST(:aid AS uuid)"),
            {"aid": alias_id})
        if r.rowcount == 0:
            raise HTTPException(status_code=404, detail="alias not found")
    invalidate_cache(str(identity.tenant_id))
    return {"ok": True}


@router.get("/audit")
async def audit_log(identity: Identity = Depends(require_identity), limit: int = 100):
    _require_admin(identity)
    async with app_session(identity.tenant_id) as s:
        r = await s.execute(text("""
            SELECT id::text, actor_id::text, action, resource, payload, ts
            FROM audit_log ORDER BY ts DESC LIMIT :limit
        """), {"limit": limit})
        return {"items": [dict(row._mapping) for row in r]}
