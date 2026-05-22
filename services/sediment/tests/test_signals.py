"""IT: signal writer endpoint + derivation cron."""
from __future__ import annotations
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from lab_lib.db import service_session
from scripts.signal_derivation import derive_signals, write_signals


pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_DB") == "1", reason="DB not available"
)


async def _seed_msg(tenant_id: str, conv_id: str, role: str, content: str, ts_offset_sec: int = 0) -> str:
    """Insert a message and return its id."""
    async with service_session() as s:
        r = await s.execute(text("""
            INSERT INTO messages (tenant_id, conv_id, role, content, ts)
            VALUES (CAST(:tid AS uuid), CAST(:cid AS uuid), :role, :content,
                    now() + (:off || ' seconds')::interval)
            RETURNING id::text
        """), {"tid": tenant_id, "cid": conv_id, "role": role,
               "content": content, "off": str(ts_offset_sec)})
        return r.first()[0]


async def _seed_conv() -> tuple[str, str]:
    async with service_session() as s:
        r = await s.execute(text("SELECT id::text FROM tenants ORDER BY created_at LIMIT 1"))
        tid = r.first()[0]
        r = await s.execute(text("""
            INSERT INTO conversations (tenant_id, title)
            VALUES (CAST(:tid AS uuid), 'signal-test')
            RETURNING id::text
        """), {"tid": tid})
        cid = r.first()[0]
    return tid, cid


# ============================================================
# derive_signals — pure function tests
# ============================================================

_BASE_TS = datetime(2026, 5, 22, 10, 0, 0, tzinfo=timezone.utc)


def _mk(role: str, content: str, secs: int, tid="t", cid="c", mid=None):
    return {
        "id": mid or f"{role}-{secs}",
        "tenant_id": tid,
        "conv_id": cid,
        "role": role,
        "content": content,
        "ts": _BASE_TS + timedelta(seconds=secs),
    }


def test_derive_emits_dwell_for_user_after_assistant():
    msgs = [
        _mk("user", "what is X?", 0),
        _mk("assistant", "X is foo [1]", 5, mid="A1"),
        _mk("user", "tell me more", 30),
    ]
    sigs = derive_signals(msgs)
    dwells = [s for s in sigs if s["kind"] == "dwell"]
    assert len(dwells) == 1
    assert dwells[0]["message_id"] == "A1"
    assert dwells[0]["value"] == 25  # 30 - 5


def test_derive_emits_re_ask_for_similar_query():
    msgs = [
        _mk("user", "what's the latest vault item?", 0),
        _mk("assistant", "unclear answer [1]", 5, mid="A1"),
        _mk("user", "what is the latest vault item ?", 60),  # similar
    ]
    sigs = derive_signals(msgs)
    re_asks = [s for s in sigs if s["kind"] == "re_ask"]
    assert len(re_asks) == 1
    assert re_asks[0]["message_id"] == "A1", "the bad assistant msg is the one flagged"
    assert re_asks[0]["meta"]["similarity"] >= 0.7


def test_derive_skips_re_ask_when_dissimilar():
    msgs = [
        _mk("user", "what's the weather in Seoul today?", 0),
        _mk("assistant", "answer A", 5, mid="A1"),
        _mk("user", "show me yesterday's meeting notes", 60),  # different
    ]
    sigs = derive_signals(msgs)
    assert not any(s["kind"] == "re_ask" for s in sigs)


def test_derive_skips_re_ask_outside_window():
    msgs = [
        _mk("user", "what's the latest vault item?", 0),
        _mk("assistant", "answer", 5, mid="A1"),
        _mk("user", "what is the latest vault item?", 600),  # >5 min later
    ]
    sigs = derive_signals(msgs, re_ask_window_sec=300)
    assert not any(s["kind"] == "re_ask" for s in sigs)


# ============================================================
# DB-level idempotency
# ============================================================

async def test_write_signals_idempotent_for_cron():
    tid, cid = await _seed_conv()
    mid = await _seed_msg(tid, cid, "assistant", "hello", 0)
    sig = {
        "tenant_id": tid, "message_id": mid, "kind": "dwell",
        "value": 30.0, "meta": {}, "source": "cron",
    }
    n1 = await write_signals([sig])
    n2 = await write_signals([sig])
    assert n1 == 1
    assert n2 == 0, "second call must be a no-op"


# ============================================================
# Full pipeline
# ============================================================

async def test_full_pipeline_against_real_db():
    """Seed a re-ask scenario into the real DB; run derivation; verify
    a re_ask row landed in message_signals."""
    tid, cid = await _seed_conv()
    u1_id = await _seed_msg(tid, cid, "user", "latest vault item?", 0)
    a1_id = await _seed_msg(tid, cid, "assistant", "unclear answer", 5)
    u2_id = await _seed_msg(tid, cid, "user", "what is the latest vault item ?", 30)

    # Load + derive + write
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT id::text, tenant_id::text, conv_id::text, role, content, ts
            FROM messages WHERE conv_id = CAST(:cid AS uuid)
            ORDER BY ts ASC
        """), {"cid": cid})
        msgs = [dict(row._mapping) for row in r]
    signals = derive_signals(msgs)
    assert any(s["kind"] == "re_ask" and s["message_id"] == a1_id for s in signals), \
        f"expected re_ask on {a1_id}, got {[(s['kind'], s['message_id']) for s in signals]}"
    n = await write_signals([s for s in signals if s["kind"] == "re_ask"])
    assert n >= 1

    # Verify the row exists in DB
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT kind, value, meta FROM message_signals
            WHERE message_id = CAST(:mid AS uuid)
        """), {"mid": a1_id})
        rows = list(r)
    assert any(row[0] == "re_ask" for row in rows)
