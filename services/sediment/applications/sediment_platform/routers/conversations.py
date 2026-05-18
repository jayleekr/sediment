"""Conversations + Messages."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from lab_lib.auth import Identity, require_identity
from lab_lib.db import app_session

router = APIRouter()


class CreateConvReq(BaseModel):
    title: str | None = None


@router.post("")
async def create(req: CreateConvReq, identity: Identity = Depends(require_identity)):
    async with app_session(identity.tenant_id) as s:
        r = await s.execute(text("""
            INSERT INTO conversations (tenant_id, user_id, title)
            VALUES (current_tenant_id(), :uid, :title) RETURNING id, created_at
        """), {"uid": identity.member_id, "title": req.title})
        row = r.first()
    return {"id": str(row[0]), "created_at": row[1].isoformat(), "title": req.title}


_SEED_TITLES_SQL = "('sec-check', 'lab-priv', 'rls-check')"


@router.get("")
async def list_convs(
    identity: Identity = Depends(require_identity),
    limit: int = 30,
    include_test: bool = Query(default=False, alias="include_test"),
):
    seed_filter = "" if include_test else f" AND title NOT IN {_SEED_TITLES_SQL}"
    sql = f"""
        SELECT id::text, user_id::text, title, created_at, updated_at
        FROM conversations
        WHERE 1=1{seed_filter}
        ORDER BY updated_at DESC LIMIT :limit
    """
    async with app_session(identity.tenant_id) as s:
        r = await s.execute(text(sql), {"limit": limit})
        return {"items": [dict(row._mapping) for row in r]}


@router.get("/{conv_id}")
async def get_one(conv_id: str, identity: Identity = Depends(require_identity)):
    async with app_session(identity.tenant_id) as s:
        c = (await s.execute(text("""
            SELECT id::text, user_id::text, title, created_at, updated_at
            FROM conversations WHERE id = :cid
        """), {"cid": conv_id})).first()
        if not c:
            raise HTTPException(status_code=404, detail="not found")
        msgs = (await s.execute(text("""
            SELECT id::text, role, content, citations, ts
            FROM messages WHERE conv_id = :cid ORDER BY ts ASC
        """), {"cid": conv_id})).fetchall()
    return {"conversation": dict(c._mapping),
            "messages": [dict(m._mapping) for m in msgs]}


class PostMessageReq(BaseModel):
    content: str
    role: str = "user"


@router.post("/{conv_id}/messages")
async def post_message(conv_id: str, req: PostMessageReq,
                       identity: Identity = Depends(require_identity)):
    async with app_session(identity.tenant_id) as s:
        r = await s.execute(text("""
            INSERT INTO messages (tenant_id, conv_id, role, content)
            VALUES (current_tenant_id(), :cid, :role, :content)
            RETURNING id, ts
        """), {"cid": conv_id, "role": req.role, "content": req.content})
        row = r.first()
        await s.execute(text("UPDATE conversations SET updated_at=now() WHERE id=:cid"),
                        {"cid": conv_id})
    return {"id": str(row[0]), "ts": row[1].isoformat()}


@router.delete("/{conv_id}")
async def delete_conv(conv_id: str, identity: Identity = Depends(require_identity)):
    async with app_session(identity.tenant_id) as s:
        await s.execute(text("DELETE FROM conversations WHERE id = :cid"),
                        {"cid": conv_id})
    return {"ok": True}
