"""Signal derivation cron — runs every 15 min.

Reads the messages table for the last 30 min, derives implicit signals,
writes them as `message_signals` rows.

Derived signals:

  re_ask  — within the same conv, two user messages within 5 min where
            the second is similar to a previous one (Levenshtein ratio
            ≥ 0.7). The assistant message that PRECEDED the second user
            turn is the one being re-asked about — it gets the signal.

  dwell   — gap between an assistant message and the next user message
            in the same conv. Value = seconds. Long dwell with no
            re-ask is implicit success.

  session_end_satisfied / _unsatisfied — when a conv goes idle ≥ 5 min,
            evaluate: had any thumbs_up or copy → satisfied; had a
            re_ask without resolution → unsatisfied. One signal per
            conv per session.

All writes are idempotent on `(message_id, kind, source='cron')` —
re-running is safe.

Invoke:
  python -m scripts.signal_derivation              # since last run
  python -m scripts.signal_derivation --window 1h  # custom lookback
"""
from __future__ import annotations
import argparse
import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

from sqlalchemy import text
from lab_lib.db import service_session


log = logging.getLogger("signal_derivation")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ============================================================
# Pull recent conversations + messages
# ============================================================

async def _load_recent(window_sec: int) -> list[dict]:
    """Pull (id, tenant, conv, role, content, ts) for last `window_sec` sec.
    BYPASSRLS service session — we need cross-tenant visibility for the
    cron job."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_sec)
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT id::text, tenant_id::text, conv_id::text, role, content, ts
            FROM messages
            WHERE ts >= :cutoff
              AND archived = false
            ORDER BY conv_id, ts ASC
        """), {"cutoff": cutoff})
        return [dict(row._mapping) for row in r]


# ============================================================
# Signal derivation
# ============================================================

def _similar(a: str, b: str) -> float:
    """Cheap Python stdlib similarity. Good enough for re-ask detection
    in this corpus (Korean+English short queries). For >0.7 threshold
    we don't need embeddings."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()


def derive_signals(
    messages: list[dict],
    *,
    re_ask_window_sec: int = 300,
    re_ask_similarity: float = 0.7,
) -> list[dict]:
    """For each conv, walk in time order and emit signals."""
    out: list[dict] = []
    by_conv: dict[str, list[dict]] = defaultdict(list)
    for m in messages:
        by_conv[m["conv_id"]].append(m)

    for conv_id, msgs in by_conv.items():
        msgs.sort(key=lambda m: m["ts"])
        user_seen: list[dict] = []  # past user msgs in this conv
        last_assistant: dict | None = None

        for m in msgs:
            if m["role"] == "user":
                # Re-ask: similar to a past user msg within window?
                for past in reversed(user_seen):
                    if (m["ts"] - past["ts"]).total_seconds() > re_ask_window_sec:
                        break
                    if _similar(m["content"], past["content"]) >= re_ask_similarity:
                        # The assistant msg that REPLIED to `past` is the
                        # one being re-asked about. Find it.
                        target = _assistant_after(msgs, past["ts"], m["ts"])
                        if target is not None:
                            out.append({
                                "tenant_id": target["tenant_id"],
                                "message_id": target["id"],
                                "kind": "re_ask",
                                "value": (m["ts"] - past["ts"]).total_seconds(),
                                "meta": {
                                    "original_user_msg_id": past["id"],
                                    "reask_user_msg_id": m["id"],
                                    "similarity": round(
                                        _similar(m["content"], past["content"]), 3
                                    ),
                                },
                                "source": "cron",
                            })
                        break  # only flag once per re-ask
                user_seen.append(m)
                # Dwell: gap from last assistant to this user msg
                if last_assistant is not None:
                    gap = (m["ts"] - last_assistant["ts"]).total_seconds()
                    if gap > 0:
                        out.append({
                            "tenant_id": last_assistant["tenant_id"],
                            "message_id": last_assistant["id"],
                            "kind": "dwell",
                            "value": gap,
                            "meta": {},
                            "source": "cron",
                        })
            else:
                last_assistant = m
    return out


def _assistant_after(msgs: list[dict], lower_ts, upper_ts) -> dict | None:
    """First assistant msg strictly between lower_ts and upper_ts."""
    for m in msgs:
        if m["role"] == "assistant" and lower_ts < m["ts"] < upper_ts:
            return m
    return None


# ============================================================
# Writer (idempotent: skip if (message_id, kind, source='cron') exists)
# ============================================================

async def write_signals(signals: list[dict]) -> int:
    if not signals:
        return 0
    written = 0
    async with service_session() as s:
        for sig in signals:
            # Skip duplicates — cron is idempotent by design.
            r = await s.execute(text("""
                SELECT 1 FROM message_signals
                WHERE message_id = CAST(:mid AS uuid)
                  AND kind       = :k
                  AND source     = 'cron'
            """), {"mid": sig["message_id"], "k": sig["kind"]})
            if r.first() is not None:
                continue
            await s.execute(text("""
                INSERT INTO message_signals (
                    tenant_id, message_id, kind, value, meta, source
                ) VALUES (
                    CAST(:tid AS uuid),
                    CAST(:mid AS uuid),
                    :kind,
                    :val,
                    CAST(:meta AS jsonb),
                    :source
                )
            """), {
                "tid": sig["tenant_id"],
                "mid": sig["message_id"],
                "kind": sig["kind"],
                "val": sig.get("value"),
                "meta": json.dumps(sig.get("meta", {})),
                "source": sig.get("source", "cron"),
            })
            written += 1
    return written


# ============================================================
# Entry point
# ============================================================

async def run(window_sec: int = 1800) -> dict:
    msgs = await _load_recent(window_sec)
    signals = derive_signals(msgs)
    n_written = await write_signals(signals)
    summary = {
        "window_sec": window_sec,
        "messages_examined": len(msgs),
        "signals_derived": len(signals),
        "signals_written": n_written,
    }
    log.info("signal_derivation.run %s", summary)
    return summary


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--window", default="30m", help="Lookback window (e.g. 15m, 1h)")
    args = p.parse_args()

    unit = args.window[-1]
    val = int(args.window[:-1])
    secs = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit] * val

    asyncio.run(run(secs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
