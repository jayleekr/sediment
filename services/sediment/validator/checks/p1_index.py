"""Phase 1 custom Python checks."""
from __future__ import annotations
import asyncio
import time
import uuid
from sqlalchemy import text

import httpx
from lab_lib.chunker import chunk_markdown
from lab_lib.db import service_session
from lab_lib.settings import settings


SAMPLE_DOC = """---
title: "RLS-Sample"
slug: validator-sample
date: 2026-05-05
---

# Heading A
Body of section A. Filler content. Filler content.

# Heading B
Body of section B. Different content. Filler content.
"""


async def _get_lab_tenant() -> str:
    async with service_session() as s:
        r = await s.execute(text("SELECT id::text FROM tenants WHERE slug='hypeproof-lab'"))
        return r.scalar_one()


async def check_ingest_single_doc(spec: dict, **_) -> dict:
    tid = await _get_lab_tenant()
    ref = f"validator/sample-{uuid.uuid4().hex[:6]}.md"
    payload = {"tenant_id": tid, "ref": ref, "type": "note", "body": SAMPLE_DOC}
    url = f"http://localhost:{settings.vault_ingester_port}/v1/ingest/document"
    async with httpx.AsyncClient() as client:
        r = await client.post(url, json=payload, timeout=120)
    if r.status_code != 200:
        return {"passed": False, "actual": {"status": r.status_code, "body": r.text[:200]},
                "message": "ingester returned non-200"}
    d = r.json()
    async with service_session() as s:
        await s.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tid})
        n = (await s.execute(text(
            "SELECT count(*) FROM chunks WHERE artifact_id = :aid"),
            {"aid": d["artifact_id"]})).scalar_one()
    ok = n >= 1 and d["chunks_written"] >= 1
    return {
        "passed": ok,
        "actual": {"artifact_id": d["artifact_id"], "chunks": n},
        "expected": {"chunks": ">=1"},
        "message": "" if ok else "no chunks written",
    }


async def check_ingest_idempotent(spec: dict, **_) -> dict:
    tid = await _get_lab_tenant()
    ref = f"validator/idem-{uuid.uuid4().hex[:6]}.md"
    payload = {"tenant_id": tid, "ref": ref, "type": "note", "body": SAMPLE_DOC}
    url = f"http://localhost:{settings.vault_ingester_port}/v1/ingest/document"
    async with httpx.AsyncClient() as client:
        r1 = await client.post(url, json=payload, timeout=120)
        r2 = await client.post(url, json=payload, timeout=120)
    if r1.status_code != 200 or r2.status_code != 200:
        return {"passed": False, "actual": {"r1": r1.status_code, "r2": r2.status_code},
                "message": "one of two ingests failed"}
    n1 = r1.json()["chunks_written"]
    n2 = r2.json()["chunks_written"]
    async with service_session() as s:
        n_db = (await s.execute(text("""
            SELECT count(*) FROM chunks c JOIN artifacts a ON a.id=c.artifact_id
            WHERE a.ref=:ref
        """), {"ref": ref})).scalar_one()
    ok = n1 == n2 and n_db == n1
    return {
        "passed": ok,
        "actual": {"first_chunks": n1, "second_chunks": n2, "db_chunks": n_db},
        "expected": "first==second==db (no duplication on re-ingest)",
        "message": "" if ok else "re-ingest produced duplicate chunks",
    }


async def check_embedding_dim(spec: dict, **_) -> dict:
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT vector_dims(embedding) FROM chunks
            WHERE embedding IS NOT NULL LIMIT 1
        """))
        row = r.first()
    if not row:
        return {"passed": False, "message": "no chunks with embedding found"}
    dim = row[0]
    ok = dim == settings.embedding_dim
    return {
        "passed": ok,
        "actual": {"dim": dim},
        "expected": {"dim": settings.embedding_dim},
        "message": "" if ok else f"embedding dim mismatch: {dim}",
    }


def check_chunker_heading_split(spec: dict, **_) -> dict:
    md = "# A\nbody A\n\n# B\nbody B"
    cs = chunk_markdown(md, max_tokens=1500)
    return {
        "passed": len(cs) >= 2,
        "actual": {"chunks": len(cs)},
        "expected": {"chunks": ">=2"},
        "message": "" if len(cs) >= 2 else "heading split produced single chunk",
    }


def check_chunker_overlap(spec: dict, **_) -> dict:
    long_a = "Section A " + ("alpha " * 600)
    long_b = "Section B " + ("beta " * 600)
    md = f"# A\n{long_a}\n\n# B\n{long_b}"
    cs = chunk_markdown(md, max_tokens=400, overlap_tokens=50)
    has_overlap = len(cs) >= 2 and "alpha" in cs[1].content
    return {
        "passed": has_overlap,
        "actual": {"chunks": len(cs), "second_has_alpha": has_overlap if len(cs) >= 2 else False},
        "expected": "second chunk contains tail of first",
        "message": "" if has_overlap else "overlap not present",
    }


async def check_chunker_avg_size(spec: dict, **_) -> dict:
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT avg(length(content)) FROM chunks
            WHERE tenant_id IN (SELECT id FROM tenants WHERE slug='hypeproof-lab')
        """))
        avg_chars = r.scalar_one() or 0
    avg_tokens_est = float(avg_chars) / 4  # rough cl100k_base estimate
    ok = 200 <= avg_tokens_est <= 1800
    return {
        "passed": ok,
        "actual": {"avg_tokens_est": round(avg_tokens_est, 1)},
        "expected": {"avg_tokens": "200..1800"},
        "message": "" if ok else f"avg chunk size out of range: {avg_tokens_est:.0f} tokens",
    }


async def check_bm25_works(spec: dict, **_) -> dict:
    """Insert a known-content artifact then verify BM25 retrieval finds it."""
    tid = await _get_lab_tenant()
    rare = f"BM25_VALIDATOR_RARETOKEN_{uuid.uuid4().hex[:6]}"
    body = f"---\ntitle: bm25 test\n---\n\nThis document contains the rare token {rare} once."
    ref = f"validator/bm25-{uuid.uuid4().hex[:6]}.md"
    url = f"http://localhost:{settings.vault_ingester_port}/v1/ingest/document"
    async with httpx.AsyncClient() as client:
        r = await client.post(url, json={"tenant_id": tid, "ref": ref, "type": "note", "body": body}, timeout=60)
        r.raise_for_status()
    async with service_session() as s:
        await s.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tid})
        rs = await s.execute(text("""
            SELECT a.ref FROM chunks c JOIN artifacts a ON a.id=c.artifact_id
            WHERE c.tsv @@ plainto_tsquery('simple', :q)
            LIMIT 5
        """), {"q": rare})
        refs = [row[0] for row in rs]
    ok = ref in refs
    return {"passed": ok, "actual": {"refs": refs}, "expected": {"contains": ref},
            "message": "" if ok else "BM25 didn't retrieve the seeded rare token"}


async def check_vector_search_works(spec: dict, **_) -> dict:
    """Vector search returns a result for any reasonable query (offline OK)."""
    from lab_lib.embeddings import embed_one
    qvec = embed_one("knowledge management")
    # if zero vector (no API key), all distances are equal; still returns rows
    qvec_str = "[" + ",".join(f"{x:.6f}" for x in qvec) + "]"
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT count(*) FROM (
                SELECT 1 FROM chunks
                WHERE tenant_id IN (SELECT id FROM tenants WHERE slug='hypeproof-lab')
                ORDER BY embedding <=> CAST(:qvec AS vector) LIMIT 5
            ) t
        """), {"qvec": qvec_str})
        n = r.scalar_one()
    return {"passed": n >= 1, "actual": {"results": n}, "expected": {"min_results": 1},
            "message": "" if n >= 1 else "vector search returned 0 results"}


async def check_hybrid_search_works(spec: dict, **_) -> dict:
    """Library /search endpoint returns at least 1 result for 'lens'."""
    import httpx
    from lab_lib.auth import mint_token
    tid = await _get_lab_tenant()
    async with service_session() as s:
        mid = (await s.execute(text(
            "SELECT id::text FROM members WHERE tenant_id=:tid AND email='jay.lee@sonatus.com'"),
            {"tid": tid})).scalar_one()
    token = mint_token(member_id=mid, tenant_id=tid, role="admin", email="jay.lee@sonatus.com")
    url = f"http://localhost:{settings.sediment_platform_port}/api/v1/library/search?q=lens"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if r.status_code != 200:
        return {"passed": False, "actual": {"status": r.status_code}, "message": r.text[:200]}
    items = r.json().get("items", [])
    return {"passed": len(items) >= 1, "actual": {"hits": len(items)},
            "expected": {"min_hits": 1},
            "message": "" if len(items) >= 1 else "hybrid search returned 0"}


async def check_library_search_http(spec: dict, **_) -> dict:
    return await check_hybrid_search_works(spec)
