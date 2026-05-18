"""Cross-tenant RLS regression checks shared by P1 / P2."""
from __future__ import annotations
import json
import time
import uuid
from sqlalchemy import text
from lab_lib.db import app_session, service_session

MARKER_LAB = "rls-marker-lab"
MARKER_ACME = "rls-marker-acme"


async def _get_tenant_id(slug: str) -> str:
    async with service_session() as s:
        r = await s.execute(text("SELECT id::text FROM tenants WHERE slug = :s"), {"s": slug})
        row = r.first()
        if not row:
            raise RuntimeError(f"tenant not seeded: {slug}")
        return row[0]


async def _seed_markers() -> tuple[str, str]:
    tid_lab = await _get_tenant_id("hypeproof-lab")
    tid_acme = await _get_tenant_id("acme-test")
    async with service_session() as s:
        await s.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tid_lab})
        await s.execute(text("""
            INSERT INTO artifacts (tenant_id, ref, type, body, frontmatter)
            VALUES (:tid, 'rls-test/lab.md', 'note', :body, '{}'::jsonb)
            ON CONFLICT (tenant_id, ref) DO UPDATE SET body = EXCLUDED.body, updated_at=now()
        """), {"tid": tid_lab, "body": MARKER_LAB})
        await s.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tid_acme})
        await s.execute(text("""
            INSERT INTO artifacts (tenant_id, ref, type, body, frontmatter)
            VALUES (:tid, 'rls-test/acme.md', 'note', :body, '{}'::jsonb)
            ON CONFLICT (tenant_id, ref) DO UPDATE SET body = EXCLUDED.body, updated_at=now()
        """), {"tid": tid_acme, "body": MARKER_ACME})
    return tid_lab, tid_acme


async def check_cross_tenant_artifacts(spec: dict, **_) -> dict:
    tid_lab, tid_acme = await _seed_markers()
    async with app_session(tid_lab) as s:
        n_acme = (await s.execute(text(
            "SELECT count(*) FROM artifacts WHERE body = :b"), {"b": MARKER_ACME})).scalar_one()
        n_lab = (await s.execute(text(
            "SELECT count(*) FROM artifacts WHERE body = :b"), {"b": MARKER_LAB})).scalar_one()
    ok = (n_acme == 0) and (n_lab == 1)
    return {
        "passed": ok,
        "actual": {"acme_visible": n_acme, "lab_visible": n_lab},
        "expected": {"acme_visible": 0, "lab_visible": 1},
        "message": "" if ok else f"LEAK: lab session sees {n_acme} acme rows",
    }


async def check_cross_tenant_chunks(spec: dict, **_) -> dict:
    tid_lab = await _get_tenant_id("hypeproof-lab")
    tid_acme = await _get_tenant_id("acme-test")
    # As acme, count chunks → must be 0 (acme has no ingest)
    async with app_session(tid_acme) as s:
        n = (await s.execute(text("SELECT count(*) FROM chunks"))).scalar_one()
    return {
        "passed": n == 0,
        "actual": {"acme_chunks_visible": n},
        "expected": {"acme_chunks_visible": 0},
        "message": "" if n == 0 else f"LEAK: acme sees {n} chunks (lab's)",
    }


async def check_no_tenant_yields_empty(spec: dict, **_) -> dict:
    """Without SET LOCAL app.tenant_id, app role should see 0 rows (fail-safe)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from lab_lib.settings import settings

    engine = create_async_engine(settings.database_url_app)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as s:
            # NO SET LOCAL — current_tenant_id() returns NULL → policy denies
            n = (await s.execute(text("SELECT count(*) FROM artifacts"))).scalar_one()
        ok = n == 0
        return {
            "passed": ok,
            "actual": {"rows_visible_without_tenant": n},
            "expected": {"rows_visible_without_tenant": 0},
            "message": "" if ok else f"FAIL-SAFE BROKEN: {n} rows visible without tenant ctx",
        }
    finally:
        await engine.dispose()


async def check_service_bypass_rls(spec: dict, **_) -> dict:
    """service role with no SET should see all artifacts (BYPASSRLS)."""
    async with service_session() as s:
        n = (await s.execute(text("SELECT count(*) FROM artifacts"))).scalar_one()
    return {
        "passed": n >= 2,  # at least the 2 RLS test markers
        "actual": {"rows_visible_as_service": n},
        "expected": {"rows_visible_as_service": ">= 2"},
        "message": "" if n >= 2 else "service role cannot see expected rows",
    }


# ============================================================
# Phase 2 — RLS during chat
# ============================================================

async def _make_token(email: str) -> tuple[str, str, str]:
    """Mint a dev token via platform. Returns (token, member_id, tenant_id)."""
    import httpx
    from lab_lib.settings import settings
    url = f"http://localhost:{settings.sediment_platform_port}/api/v1/auth/dev-token"
    async with httpx.AsyncClient() as client:
        r = await client.post(url, json={"email": email}, timeout=5)
        r.raise_for_status()
        d = r.json()
    return d["token"], d["member_id"], d["tenant_id"]


async def check_conversation_tenant_match(spec: dict, **_) -> dict:
    import httpx
    from lab_lib.settings import settings
    token, mid, tid = await _make_token("jay.lee@sonatus.com")
    base = f"http://localhost:{settings.sediment_platform_port}/api/v1"
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{base}/conversations", json={"title": "rls-check"},
                               headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        cid = r.json()["id"]
    async with service_session() as s:
        row = (await s.execute(text(
            "SELECT tenant_id::text FROM conversations WHERE id = :cid"), {"cid": cid})).first()
    return {
        "passed": row is not None and row[0] == tid,
        "actual": {"conv_tenant": row[0] if row else None, "expected_tenant": tid},
        "message": "" if row and row[0] == tid else "tenant_id mismatch",
    }


async def check_cross_tenant_conversation_blocked(spec: dict, **_) -> dict:
    """Lab user creates conversation. Acme user attempts to GET it → must fail."""
    import httpx
    from lab_lib.settings import settings
    base = f"http://localhost:{settings.sediment_platform_port}/api/v1"

    lab_token, _, _ = await _make_token("jay.lee@sonatus.com")
    # Create acme test member if missing
    async with service_session() as s:
        row = (await s.execute(text(
            "SELECT email FROM members m JOIN tenants t ON t.id=m.tenant_id "
            "WHERE t.slug='acme-test' LIMIT 1"))).first()
    if not row:
        return {"passed": False, "message": "acme-test member missing — run make seed"}
    acme_email = row[0]
    acme_token, _, _ = await _make_token(acme_email)

    async with httpx.AsyncClient() as client:
        r = await client.post(f"{base}/conversations", json={"title": "lab-priv"},
                               headers={"Authorization": f"Bearer {lab_token}"})
        r.raise_for_status()
        cid = r.json()["id"]

        r2 = await client.get(f"{base}/conversations/{cid}",
                              headers={"Authorization": f"Bearer {acme_token}"})
    ok = r2.status_code == 404
    return {
        "passed": ok,
        "actual": {"acme_get_status": r2.status_code},
        "expected": {"acme_get_status": 404},
        "message": "" if ok else f"LEAK: acme reads lab conversation, status={r2.status_code}",
    }


async def check_cross_tenant_messages_blocked(spec: dict, **_) -> dict:
    """Acme user listing /conversations should not see lab conversations."""
    import httpx
    from lab_lib.settings import settings
    base = f"http://localhost:{settings.sediment_platform_port}/api/v1"

    async with service_session() as s:
        row = (await s.execute(text(
            "SELECT email FROM members m JOIN tenants t ON t.id=m.tenant_id "
            "WHERE t.slug='acme-test' LIMIT 1"))).first()
    if not row:
        return {"passed": False, "message": "acme member missing"}

    acme_token, _, _ = await _make_token(row[0])

    async with httpx.AsyncClient() as client:
        r = await client.get(f"{base}/conversations",
                              headers={"Authorization": f"Bearer {acme_token}"})
        r.raise_for_status()
        items = r.json()["items"]

    titles = [it.get("title", "") for it in items]
    lab_priv_visible = any(t == "lab-priv" or t == "rls-check" for t in titles)
    return {
        "passed": not lab_priv_visible,
        "actual": {"acme_sees_lab_titles": [t for t in titles if t in ("lab-priv", "rls-check")]},
        "expected": {"lab_titles_visible_to_acme": []},
        "message": "" if not lab_priv_visible else "LEAK: acme sees lab conversations",
    }
