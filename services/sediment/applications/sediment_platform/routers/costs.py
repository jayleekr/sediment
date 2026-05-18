"""Cost / usage summary (admin only)."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from lab_lib.auth import Identity, require_identity
from lab_lib.db import app_session

router = APIRouter()


@router.get("/summary")
async def summary(identity: Identity = Depends(require_identity)):
    if identity.role != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    async with app_session(identity.tenant_id) as s:
        r = await s.execute(text("""
            SELECT date, query_count, tokens_total, cost_cents
            FROM usage_daily
            ORDER BY date DESC LIMIT 60
        """))
        days = [dict(row._mapping) for row in r]
        r2 = await s.execute(text("""
            SELECT COALESCE(sum(cost_cents), 0) AS month_cost_cents,
                   COALESCE(sum(query_count), 0) AS month_query_count
            FROM usage_daily
            WHERE date >= date_trunc('month', current_date)
        """))
        month = dict(r2.first()._mapping)
    return {"month": month, "days": days}
