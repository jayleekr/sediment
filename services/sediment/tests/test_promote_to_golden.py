"""IT: /api/v1/feedback/promote-to-golden."""
from __future__ import annotations
import json
import os

import httpx
import pytest
from sqlalchemy import text

from applications.sediment_platform.main import app
from lab_lib.db import service_session


pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_DB") == "1", reason="DB not available"
)


async def _mint_token(email: str = "jay.lee@sonatus.com") -> str:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/v1/auth/dev-token", json={"email": email})
        r.raise_for_status()
        return r.json()["token"]


async def _seed_full_exchange() -> tuple[str, str, str]:
    """Seed a (user msg, assistant msg) pair and return (tenant, conv, assistant_msg_id)."""
    async with service_session() as s:
        r = await s.execute(text("SELECT id::text FROM tenants ORDER BY created_at LIMIT 1"))
        tid = r.first()[0]
        # need a member
        r = await s.execute(text("""
            SELECT id::text FROM members
            WHERE tenant_id = CAST(:tid AS uuid) ORDER BY created_at LIMIT 1
        """), {"tid": tid})
        uid = r.first()[0]
        # conv
        r = await s.execute(text("""
            INSERT INTO conversations (tenant_id, user_id, title)
            VALUES (CAST(:tid AS uuid), CAST(:uid AS uuid), 'promote-test')
            RETURNING id::text
        """), {"tid": tid, "uid": uid})
        cid = r.first()[0]
        # user msg — give it an explicit earlier ts so the join in
        # promote_to_golden.py (u.ts < m.ts) finds it
        await s.execute(text("""
            INSERT INTO messages (tenant_id, conv_id, role, content, ts)
            VALUES (CAST(:tid AS uuid), CAST(:cid AS uuid), 'user', :q,
                    now() - interval '5 seconds')
        """), {"tid": tid, "cid": cid, "q": "가장 최근 볼트?"})
        # assistant msg (bad answer)
        r = await s.execute(text("""
            INSERT INTO messages (
                tenant_id, conv_id, role, content, citations, intent, grounding_status
            )
            VALUES (
                CAST(:tid AS uuid), CAST(:cid AS uuid), 'assistant',
                '잘못된 답변', CAST('[]' AS jsonb), 'library', 'no_evidence'
            )
            RETURNING id::text
        """), {"tid": tid, "cid": cid})
        mid = r.first()[0]
    return tid, cid, mid


async def test_promote_creates_proposal_and_thumbs_down():
    tid, cid, mid = await _seed_full_exchange()
    token = await _mint_token()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/v1/feedback/promote-to-golden",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "conv_id": cid,
                "message_id": mid,
                "reason": "Library intent picked but query is meta-style; vault stats expected",
                "expected_intent": "freshness",
            },
        )
        assert r.status_code == 204, r.text

    async with service_session() as s:
        # 1. Proposal event row
        r = await s.execute(text("""
            SELECT kind, payload FROM events
            WHERE kind = 'golden.proposal'
              AND payload->>'message_id' = :mid
            ORDER BY ts DESC LIMIT 1
        """), {"mid": mid})
        row = r.first()
        assert row is not None, "no golden.proposal event row created"
        kind, payload = row
        assert kind == "golden.proposal"
        assert payload["expected_intent"] == "freshness"
        assert "Library intent picked" in payload["reason"]

        # 2. thumbs_down signal also dropped (so daily judge sees it)
        r = await s.execute(text("""
            SELECT kind, meta FROM message_signals
            WHERE message_id = CAST(:mid AS uuid) AND kind = 'thumbs_down'
        """), {"mid": mid})
        sig_row = r.first()
        assert sig_row is not None, "promote-to-golden didn't write thumbs_down"
        _, meta = sig_row
        assert meta["promoted_to_golden"] is True


async def test_promote_unknown_message_404():
    token = await _mint_token()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/v1/feedback/promote-to-golden",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "conv_id": "00000000-0000-0000-0000-000000000000",
                "message_id": "00000000-0000-0000-0000-000000000000",
                "reason": "test",
            },
        )
        assert r.status_code == 404
