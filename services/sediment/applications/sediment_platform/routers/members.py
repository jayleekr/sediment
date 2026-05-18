"""Members directory."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from lab_lib.auth import Identity, require_identity
from lab_lib.db import app_session

router = APIRouter()


@router.get("")
async def list_members(identity: Identity = Depends(require_identity)):
    async with app_session(identity.tenant_id) as s:
        r = await s.execute(text("""
            SELECT id::text, external_id, email, display_name, real_name,
                   role, title, expertise, interests
            FROM members ORDER BY display_name
        """))
        return {"items": [dict(row._mapping) for row in r]}


@router.get("/me")
async def me(identity: Identity = Depends(require_identity)):
    async with app_session(identity.tenant_id) as s:
        r = await s.execute(text("""
            SELECT id::text, external_id, email, display_name, real_name,
                   role, title, expertise, interests
            FROM members WHERE id = :mid
        """), {"mid": identity.member_id})
        row = r.first()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        return dict(row._mapping)


@router.get("/{member_id}/profile")
async def profile(member_id: str, identity: Identity = Depends(require_identity)):
    """Member profile + recent contributions + open actions."""
    async with app_session(identity.tenant_id) as s:
        m = (await s.execute(text("""
            SELECT id::text, external_id, email, display_name, real_name,
                   role, title, expertise, interests
            FROM members WHERE id = :mid
        """), {"mid": member_id})).first()
        if not m:
            raise HTTPException(status_code=404, detail="not found")

        recent = (await s.execute(text("""
            SELECT ref, type, date, slug FROM artifacts
            WHERE author_id = :mid ORDER BY date DESC NULLS LAST LIMIT 10
        """), {"mid": member_id})).fetchall()

        actions = (await s.execute(text("""
            SELECT id::text, description, due_date, status
            FROM actions WHERE owner_id = :mid AND status IN ('open','in_progress')
            ORDER BY due_date NULLS LAST LIMIT 20
        """), {"mid": member_id})).fetchall()

    return {
        "member": dict(m._mapping),
        "recent_artifacts": [dict(r._mapping) for r in recent],
        "open_actions": [dict(a._mapping) for a in actions],
    }
