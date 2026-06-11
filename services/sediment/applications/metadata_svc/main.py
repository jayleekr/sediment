"""Metadata Service :12000

Frontmatter index queries: by-author, by-type, by-date, by-lens, by-tag, cooccurrence.
RLS-enforced (uses app role).
"""
from __future__ import annotations
from datetime import date as Date
from typing import Optional
from fastapi import Depends, FastAPI, Query
from sqlalchemy import text

from lab_lib.auth import Identity, require_identity
from lab_lib.db import app_session
from lab_lib.logging import configure_logging, get_logger
from lab_lib.tenant_middleware import TenantContextMiddleware

configure_logging()
log = get_logger("metadata")

app = FastAPI(title="Sediment Metadata Service", version="0.1.0")
app.add_middleware(TenantContextMiddleware)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/v1/meta/by-author/{external_id}")
async def by_author(external_id: str, identity: Identity = Depends(require_identity)):
    async with app_session(identity.tenant_id) as s:
        r = await s.execute(text("""
            SELECT a.ref, a.type, a.date, a.slug, a.lang, a.frontmatter
            FROM artifacts a JOIN members m ON m.id = a.author_id
            WHERE m.external_id = :eid
            ORDER BY a.date DESC NULLS LAST
            LIMIT 200
        """), {"eid": external_id})
        return {"items": [dict(row._mapping) for row in r]}


@app.get("/v1/meta/by-type/{type}")
async def by_type(type: str, limit: int = Query(default=50, le=500),
                  identity: Identity = Depends(require_identity)):
    async with app_session(identity.tenant_id) as s:
        r = await s.execute(text("""
            SELECT ref, type, author_id, date, slug, lang, frontmatter
            FROM artifacts WHERE type = :t
            ORDER BY date DESC NULLS LAST LIMIT :limit
        """), {"t": type, "limit": limit})
        return {"items": [dict(row._mapping) for row in r]}


@app.get("/v1/meta/by-date")
async def by_date(start: Date = Query(...), end: Date = Query(...),
                  identity: Identity = Depends(require_identity)):
    async with app_session(identity.tenant_id) as s:
        r = await s.execute(text("""
            SELECT ref, type, author_id, date, slug
            FROM artifacts
            WHERE date BETWEEN :s AND :e
            ORDER BY date DESC LIMIT 500
        """), {"s": start, "e": end})
        return {"items": [dict(row._mapping) for row in r]}


@app.get("/v1/meta/by-lens/{lens}")
async def by_lens(lens: str, identity: Identity = Depends(require_identity)):
    async with app_session(identity.tenant_id) as s:
        r = await s.execute(text("""
            SELECT ref, type, date, slug, frontmatter
            FROM artifacts
            WHERE frontmatter @> jsonb_build_object('lens', jsonb_build_array(:lens))
               OR frontmatter -> 'lens' ? :lens
            ORDER BY date DESC NULLS LAST LIMIT 200
        """), {"lens": lens})
        return {"items": [dict(row._mapping) for row in r]}


@app.get("/v1/meta/tags/cooccurrence")
async def cooccurrence(limit: int = 50, identity: Identity = Depends(require_identity)):
    async with app_session(identity.tenant_id) as s:
        r = await s.execute(text("""
            WITH pairs AS (
              SELECT a, b FROM (
                SELECT id, jsonb_array_elements_text(frontmatter -> 'topic') AS a
                FROM artifacts WHERE jsonb_typeof(frontmatter -> 'topic') = 'array'
              ) t1 JOIN LATERAL (
                SELECT jsonb_array_elements_text(frontmatter -> 'topic') AS b
                FROM artifacts WHERE id = t1.id
              ) t2 ON true
              WHERE a < b
            )
            SELECT a, b, count(*) AS n FROM pairs
            GROUP BY a, b ORDER BY n DESC LIMIT :limit
        """), {"limit": limit})
        return {"pairs": [dict(row._mapping) for row in r]}


@app.get("/v1/meta/summary")
async def summary(identity: Identity = Depends(require_identity)):
    async with app_session(identity.tenant_id) as s:
        r = await s.execute(text("""
            SELECT type, count(*) AS n FROM artifacts GROUP BY type ORDER BY n DESC
        """))
        type_counts = [dict(row._mapping) for row in r]
        r2 = await s.execute(text("""
            SELECT count(*) AS n_artifacts,
                   (SELECT count(*) FROM chunks) AS n_chunks,
                   (SELECT count(*) FROM members) AS n_members
            FROM artifacts
        """))
        head = dict(r2.first()._mapping)
    return {**head, "by_type": type_counts}
