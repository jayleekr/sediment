"""Vault Ingester :11000

RAG pipeline: read → frontmatter parse → chunk → embed → pgvector.
Tenant-aware: all writes carry tenant_id, RLS enforced.

Uses service role (BYPASSRLS) since ingest is a system task.
"""
from __future__ import annotations
import asyncio
import datetime as _dt
import hashlib
import hmac
import os
import time
from typing import Optional, Union
import frontmatter
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text

from lab_lib.chunker import chunk_markdown
from lab_lib.db import service_session
from lab_lib.embeddings import embed


def _coerce_date(v: Union[str, _dt.date, _dt.datetime, None]) -> Optional[_dt.date]:
    """artifacts.date is a Postgres DATE column. asyncpg needs a datetime.date,
    not a str — passing 'YYYY-MM-DD' raises DataError ('str' has no toordinal).
    python-frontmatter returns a date object for `date: 2026-03-22` but a str
    for quoted/odd values, so normalize both. Bad/empty → None (nullable col).
    """
    if v is None or v == "":
        return None
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    s = str(v).strip()[:10]
    try:
        return _dt.date.fromisoformat(s)
    except ValueError:
        return None
from lab_lib.logging import configure_logging, get_logger
from lab_lib.settings import settings

configure_logging()
log = get_logger("ingester")

app = FastAPI(title="Curator Vault Ingester", version="0.1.0")


class IngestDocReq(BaseModel):
    tenant_id: str
    ref: str                 # repo-relative path or canonical id
    type: str                # 'column'|'research'|'novel'|'note'|'meeting'|...
    body: str                # raw markdown (with frontmatter)
    author_external_id: Optional[str] = None  # Discord ID or members.id


class IngestDocResp(BaseModel):
    artifact_id: str
    chunks_written: int
    elapsed_ms: int


class IngestBatchReq(BaseModel):
    tenant_id: str
    docs: list[IngestDocReq]


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


async def _resolve_author(s, tenant_id: str, external_id: Optional[str]) -> Optional[str]:
    if not external_id:
        return None
    r = await s.execute(text(
        "SELECT id FROM members WHERE tenant_id = :tid AND external_id = :eid"),
        {"tid": tenant_id, "eid": external_id})
    row = r.first()
    return str(row[0]) if row else None


async def _delete_existing_chunks(s, artifact_id: str):
    await s.execute(text("DELETE FROM chunks WHERE artifact_id = :aid"), {"aid": artifact_id})


@app.post("/v1/ingest/document", response_model=IngestDocResp)
async def ingest_document(req: IngestDocReq):
    t0 = time.time()
    fm = frontmatter.loads(req.body)
    fmdict = dict(fm.metadata)
    body_only = fm.content
    chunks = chunk_markdown(body_only)

    if not chunks:
        raise HTTPException(status_code=400, detail="document produced no chunks")

    embeddings = embed([c.content for c in chunks])

    async with service_session() as s:
        # set context so policies behave consistently (service role bypasses but be explicit)
        await s.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": req.tenant_id})

        author_id = await _resolve_author(s, req.tenant_id, req.author_external_id)

        # upsert artifact by (tenant_id, ref)
        r = await s.execute(text("""
            INSERT INTO artifacts (tenant_id, ref, type, author_id, date, slug, lang, frontmatter, body, status, updated_at)
            VALUES (:tid, :ref, :typ, :aid, :date, :slug, :lang, :fm, :body, COALESCE(:status, 'published'), now())
            ON CONFLICT (tenant_id, ref) DO UPDATE SET
                type = EXCLUDED.type,
                author_id = EXCLUDED.author_id,
                date = EXCLUDED.date,
                slug = EXCLUDED.slug,
                lang = EXCLUDED.lang,
                frontmatter = EXCLUDED.frontmatter,
                body = EXCLUDED.body,
                status = EXCLUDED.status,
                updated_at = now()
            RETURNING id
        """), {
            "tid": req.tenant_id,
            "ref": req.ref,
            "typ": req.type,
            "aid": author_id,
            "date": _coerce_date(fmdict.get("date")),
            "slug": fmdict.get("slug"),
            "lang": fmdict.get("lang"),
            "fm": _json(fmdict),
            "body": body_only,
            "status": fmdict.get("status"),
        })
        artifact_id = str(r.scalar_one())

        await _delete_existing_chunks(s, artifact_id)

        # bulk insert chunks
        for c, vec in zip(chunks, embeddings):
            await s.execute(text("""
                INSERT INTO chunks (tenant_id, artifact_id, seq, content, embedding)
                VALUES (:tid, :aid, :seq, :content, :emb)
            """), {
                "tid": req.tenant_id,
                "aid": artifact_id,
                "seq": c.seq,
                "content": c.content,
                "emb": _vec(vec),
            })

    elapsed = int((time.time() - t0) * 1000)
    log.info("ingest.document.ok", ref=req.ref, chunks=len(chunks), elapsed_ms=elapsed)
    return IngestDocResp(artifact_id=artifact_id, chunks_written=len(chunks), elapsed_ms=elapsed)


@app.post("/v1/ingest/batch")
async def ingest_batch(req: IngestBatchReq):
    sem = asyncio.Semaphore(4)

    async def one(d: IngestDocReq):
        async with sem:
            try:
                return {"ref": d.ref, "ok": True, **(await ingest_document(d)).model_dump()}
            except Exception as e:
                log.exception("ingest.fail", ref=d.ref)
                return {"ref": d.ref, "ok": False, "error": str(e)}

    results = await asyncio.gather(*[one(d) for d in req.docs])
    return {"count": len(results), "results": results}


@app.delete("/v1/chunks")
async def delete_chunks(tenant_id: str, ref: str):
    async with service_session() as s:
        await s.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id})
        r = await s.execute(text("""
            DELETE FROM chunks WHERE artifact_id IN (
              SELECT id FROM artifacts WHERE tenant_id = :tid AND ref = :ref
            )
        """), {"tid": tenant_id, "ref": ref})
    return {"ok": True, "rows": r.rowcount}


# ----- helpers -----
def _json(d):
    import json
    return json.dumps(d, default=str)


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


# ============================================================
# P1-L1: GitHub push → vault freshness webhook
# nginx already routes /webhook/ → this service (infra/deploy/nginx.conf).
# The GitHub Action (.github/workflows/vault-ingest.yml) computes the changed
# files and POSTs them here with content, so this endpoint never has to call
# back to GitHub. Idempotent: ingest_document upserts by (tenant_id, ref).
# ============================================================
class WebhookFile(BaseModel):
    path: str
    content: Optional[str] = None      # None when deleted=True
    deleted: bool = False


class WebhookIngestReq(BaseModel):
    ref: str = "unknown"               # git ref / sha, for the audit event
    files: list[WebhookFile]


async def _default_tenant_id() -> Optional[str]:
    async with service_session() as s:
        r = await s.execute(
            text("SELECT id::text FROM tenants WHERE slug = :slug"),
            {"slug": settings.default_tenant_slug},
        )
        row = r.first()
        return row[0] if row else None


def _verify_github_sig(raw: bytes, header: Optional[str]) -> bool:
    """HMAC-SHA256 of the raw body against GITHUB_WEBHOOK_SECRET.
    If the secret is unset, skip the check (start.sh already WARNs about this)
    so local/un-provisioned envs still function — matches existing behaviour.
    """
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
    if not secret:
        log.warning("webhook.no_secret — signature check skipped")
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


@app.post("/webhook/ingest")
async def webhook_ingest(request: Request):
    raw = await request.body()
    if not _verify_github_sig(raw, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=401, detail="bad webhook signature")
    try:
        req = WebhookIngestReq.model_validate_json(raw)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"bad payload: {e}")

    tid = await _default_tenant_id()
    if not tid:
        raise HTTPException(status_code=500, detail="default tenant not found — run `make seed`")

    # Canonical filters from lab_lib (pure helpers, no vault-root dependency).
    # Previously imported from scripts.ingest_repo, but that module raises
    # SystemExit at import time when SEDIMENT_VAULT_ROOT is unset and no
    # vault is on disk — true inside this container (vault lives in
    # hypeprooflab, a SEPARATE repo) → webhook 500. lab_lib.vault_paths is
    # pure logic and always importable.
    from lab_lib.vault_paths import detect_type, is_excluded

    to_ingest: list[IngestDocReq] = []
    deleted: list[str] = []
    skipped = 0
    for f in req.files:
        parts = tuple(f.path.split("/"))
        if not f.path.endswith(".md") or is_excluded(parts):
            skipped += 1
            continue
        if f.deleted:
            deleted.append(f.path)
        elif f.content is not None:
            to_ingest.append(IngestDocReq(
                tenant_id=tid, ref=f.path, type=detect_type(f.path), body=f.content,
            ))
        else:
            skipped += 1

    result = {"ok": True}
    if to_ingest:
        result["ingested"] = await ingest_batch(IngestBatchReq(tenant_id=tid, docs=to_ingest))
    for ref in deleted:
        try:
            await delete_chunks(tenant_id=tid, ref=ref)
        except Exception as e:
            log.warning("webhook.delete.fail", ref=ref, err=str(e))

    n_ok = sum(1 for r in result.get("ingested", {}).get("results", []) if r.get("ok"))
    # Freshness breadcrumb — queried by /v1/vault/freshness + surfaced in UI so
    # staleness is visible, not silent (ACTIVATION_ENGINE.md P1 freshness metric).
    async with service_session() as s:
        await s.execute(text("""
            INSERT INTO events (tenant_id, source, kind, payload)
            VALUES (:tid, 'github', 'vault.ingest', CAST(:p AS jsonb))
        """), {"tid": tid, "p": _json({
            "ref": req.ref, "n_ingested": n_ok,
            "n_deleted": len(deleted), "n_skipped": skipped,
        })})

    log.info("webhook.ingest.done", ref=req.ref, ingested=n_ok,
             deleted=len(deleted), skipped=skipped)
    return {**result, "n_ingested": n_ok, "n_deleted": len(deleted),
            "n_skipped": skipped, "ref": req.ref}
    # NOTE: the freshness READ endpoint lives on the PLATFORM
    # (routers/vault.py → GET /api/v1/vault/freshness), not here — nginx only
    # routes /webhook/ to this ingester, so a read here is browser-unreachable.
