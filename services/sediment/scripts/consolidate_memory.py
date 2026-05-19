"""Memory consolidation worker — Phase 4.

Reads recent conversations and extracts structured decisions + actions via
Anthropic tool-use. Inserts them into the `decisions` and `actions` tables
with provenance (conv_id link) so future queries can surface them directly
without re-walking the chat history.

Idempotent: skip when (tenant_id, topic, conv_id) already has a decision row.

CLI:
  python -m scripts.consolidate_memory --tenant hypeproof-lab --since-hours 24
  python -m scripts.consolidate_memory --tenant hypeproof-lab --since-hours 168 --dry-run
  python -m scripts.consolidate_memory --conv-id <uuid>      # one specific conv

Exit codes:
  0  ok (or no work)
  1  config error (missing key / tenant)
  2  anthropic error (rate limit / network)
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import text

from lab_lib.db import service_session
from lab_lib.logging import configure_logging, get_logger
from lab_lib.settings import settings

configure_logging()
log = get_logger("consolidate")


# Anthropic tool schema — forces the LLM to return structured JSON.
# Each conversation can produce 0..N decisions, each with 0..N actions.
_EXTRACT_TOOL = {
    "name": "record_decisions_and_actions",
    "description": (
        "Record any explicit decisions and follow-up actions surfaced in the "
        "conversation. Skip passing remarks and questions. A 'decision' is a "
        "commitment to a course of action ('we'll use X', 'let's go with Y', "
        "'결정됨: …'). An 'action' is a task assigned to a person with optional "
        "due date. ONLY emit items that the conversation clearly establishes; "
        "do not invent."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string",
                                   "description": "Short headline, max 80 chars."},
                        "body": {"type": "string",
                                  "description": "1-3 sentence rationale + outcome."},
                        "status": {"type": "string",
                                    "enum": ["open", "made", "reverted"],
                                    "description": "Default 'made' when clearly decided."},
                    },
                    "required": ["topic", "body"],
                },
            },
            "actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "owner_hint": {"type": "string",
                                        "description": "Member display name or email "
                                                       "if mentioned, else null."},
                        "due_date": {"type": "string",
                                      "description": "YYYY-MM-DD if explicit, else null."},
                        "decision_topic": {"type": "string",
                                            "description": "Which decision (from above) "
                                                           "this action follows from, "
                                                           "or null if standalone."},
                    },
                    "required": ["description"],
                },
            },
        },
        "required": ["decisions", "actions"],
    },
}

_SYSTEM = (
    "You are a meeting/chat memory consolidator. Extract decisions and actions "
    "that were CLEARLY made in this conversation. If the conversation is just "
    "Q&A with no commitments, return empty arrays — do not invent decisions to "
    "fill the schema. Keep topics concrete (no 'figure out X' as a decision). "
    "Output language: match the conversation's primary language."
)


async def _list_conversations(since: datetime, conv_id: Optional[str], tenant_slug: str) -> list[dict]:
    async with service_session() as s:
        if conv_id:
            r = await s.execute(text("""
                SELECT c.id::text, c.tenant_id::text, c.title, c.updated_at
                FROM conversations c JOIN tenants t ON t.id = c.tenant_id
                WHERE c.id = :cid AND t.slug = :slug
                LIMIT 1
            """), {"cid": conv_id, "slug": tenant_slug})
        else:
            r = await s.execute(text("""
                SELECT c.id::text, c.tenant_id::text, c.title, c.updated_at
                FROM conversations c JOIN tenants t ON t.id = c.tenant_id
                WHERE t.slug = :slug AND c.updated_at >= :since
                ORDER BY c.updated_at DESC
            """), {"slug": tenant_slug, "since": since})
        return [dict(row._mapping) for row in r]


async def _load_messages(conv_id: str) -> list[dict]:
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT role, content, ts
            FROM messages
            WHERE conv_id = :cid AND role IN ('user', 'assistant')
            ORDER BY ts ASC
        """), {"cid": conv_id})
        return [{"role": row[0], "content": row[1] or "", "ts": row[2]} for row in r]


async def _already_consolidated(tenant_id: str, conv_id: str) -> bool:
    """A conv is treated as consolidated if it already has at least one decision
    row tagged with conv_id. Re-running won't blow up — _insert_decision dedupes
    on (tenant_id, topic, conv_id) — but the LLM call is the expensive bit, so
    short-circuit here saves token cost.
    """
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT 1 FROM decisions
            WHERE tenant_id = :tid AND conv_id = :cid
            LIMIT 1
        """), {"tid": tenant_id, "cid": conv_id})
        return r.first() is not None


async def _extract(messages: list[dict]) -> dict:
    if not settings.anthropic_api_key or settings.anthropic_api_key == "sk-ant-...":
        raise RuntimeError("ANTHROPIC_API_KEY not configured")
    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    transcript = "\n\n".join(
        f"[{m['role']}] {m['content']}" for m in messages if (m.get("content") or "").strip()
    )
    if len(transcript) < 50:
        return {"decisions": [], "actions": []}
    resp = await client.messages.create(
        model=settings.llm_model_default,
        max_tokens=2048,
        system=_SYSTEM,
        tools=[_EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": _EXTRACT_TOOL["name"]},
        messages=[{"role": "user", "content": transcript[:32000]}],
    )
    # Walk content blocks for the tool_use block
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use":
            return block.input  # already a dict per Anthropic SDK
    return {"decisions": [], "actions": []}


async def _resolve_owner(tenant_id: str, hint: Optional[str]) -> Optional[str]:
    if not hint:
        return None
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT id::text FROM members
            WHERE tenant_id = :tid
              AND (display_name = :h OR real_name = :h OR email = :h)
            LIMIT 1
        """), {"tid": tenant_id, "h": hint})
        row = r.first()
        return row[0] if row else None


async def _insert_decision(tenant_id: str, conv_id: Optional[str], topic: str,
                            body: str, status: str) -> Optional[str]:
    """Insert or return existing id. Dedup key: (tenant_id, topic, conv_id).

    conv_id is NULL for event-sourced decisions (Discord #weekly etc. via
    distill.py). In SQL, `conv_id = NULL` is never true, so a plain equality
    dedup would insert a fresh row on every re-run → unbounded duplicates.
    Branch on NULL to use `IS NULL` (idempotent re-runs)."""
    async with service_session() as s:
        if conv_id is None:
            existing = await s.execute(text("""
                SELECT id::text FROM decisions
                WHERE tenant_id = :tid AND topic = :topic AND conv_id IS NULL
                LIMIT 1
            """), {"tid": tenant_id, "topic": topic})
        else:
            existing = await s.execute(text("""
                SELECT id::text FROM decisions
                WHERE tenant_id = :tid AND topic = :topic AND conv_id = :cid
                LIMIT 1
            """), {"tid": tenant_id, "topic": topic, "cid": conv_id})
        row = existing.first()
        if row:
            return row[0]
        r = await s.execute(text("""
            INSERT INTO decisions (tenant_id, topic, body, status, conv_id, made_at)
            VALUES (:tid, :topic, :body, :status, :cid, now())
            RETURNING id::text
        """), {"tid": tenant_id, "topic": topic, "body": body,
                "status": status, "cid": conv_id})
        await s.commit()
        return r.scalar_one()


async def _insert_action(tenant_id: str, decision_id: Optional[str],
                          owner_id: Optional[str], description: str,
                          due_date: Optional[str]) -> None:
    async with service_session() as s:
        # Dedupe: same description under same decision_id (or same tenant if no decision)
        if decision_id:
            existing = await s.execute(text("""
                SELECT 1 FROM actions
                WHERE tenant_id = :tid AND decision_id = :did
                  AND description = :desc LIMIT 1
            """), {"tid": tenant_id, "did": decision_id, "desc": description})
        else:
            existing = await s.execute(text("""
                SELECT 1 FROM actions
                WHERE tenant_id = :tid AND decision_id IS NULL
                  AND description = :desc LIMIT 1
            """), {"tid": tenant_id, "desc": description})
        if existing.first():
            return
        # Resolve due_date in Python — avoids asyncpg's AmbiguousParameterError
        # when the same :due param is used in both NULL-check and CAST contexts.
        from datetime import datetime as _dt
        due_parsed = None
        if due_date and isinstance(due_date, str) and len(due_date) == 10:
            try:
                due_parsed = _dt.strptime(due_date, "%Y-%m-%d").date()
            except ValueError:
                due_parsed = None
        await s.execute(text("""
            INSERT INTO actions (tenant_id, decision_id, owner_id, description, due_date)
            VALUES (:tid, :did, :oid, :desc, :due)
        """), {"tid": tenant_id, "did": decision_id, "oid": owner_id,
                "desc": description, "due": due_parsed})
        await s.commit()


async def consolidate_conv(conv: dict, dry_run: bool = False) -> dict:
    tenant_id = conv["tenant_id"]
    conv_id = conv["id"]

    if not dry_run and await _already_consolidated(tenant_id, conv_id):
        log.info("consolidate.skip.already", conv_id=conv_id)
        return {"conv_id": conv_id, "status": "already", "decisions": 0, "actions": 0}

    msgs = await _load_messages(conv_id)
    if len(msgs) < 2:
        return {"conv_id": conv_id, "status": "too_short", "decisions": 0, "actions": 0}

    extracted = await _extract(msgs)
    decisions = extracted.get("decisions") or []
    actions = extracted.get("actions") or []

    if dry_run:
        return {"conv_id": conv_id, "status": "dry_run",
                "decisions": len(decisions), "actions": len(actions),
                "preview": {"decisions": decisions[:3], "actions": actions[:3]}}

    topic_to_decision_id: dict[str, str] = {}
    for d in decisions:
        did = await _insert_decision(
            tenant_id, conv_id,
            d["topic"][:200], d.get("body", "")[:2000],
            d.get("status", "made"),
        )
        if did:
            topic_to_decision_id[d["topic"]] = did

    inserted_actions = 0
    for a in actions:
        owner_id = await _resolve_owner(tenant_id, a.get("owner_hint"))
        decision_id = topic_to_decision_id.get(a.get("decision_topic") or "")
        try:
            await _insert_action(
                tenant_id, decision_id, owner_id,
                a["description"][:1000], a.get("due_date"),
            )
            inserted_actions += 1
        except Exception as e:
            log.warning("consolidate.action.insert_failed",
                         conv_id=conv_id, err=str(e)[:120])

    log.info("consolidate.done", conv_id=conv_id,
              decisions=len(decisions), actions=inserted_actions)
    return {"conv_id": conv_id, "status": "ok",
             "decisions": len(decisions), "actions": inserted_actions}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default="hypeproof-lab",
                     help="tenant slug (default: hypeproof-lab)")
    ap.add_argument("--since-hours", type=int, default=24,
                     help="look-back window in hours (default: 24)")
    ap.add_argument("--conv-id", help="consolidate one specific conv_id and exit")
    ap.add_argument("--dry-run", action="store_true",
                     help="extract but don't insert; print preview")
    ap.add_argument("--limit", type=int, default=50,
                     help="max conversations per run")
    args = ap.parse_args()

    since = datetime.now(timezone.utc) - timedelta(hours=args.since_hours)
    convs = await _list_conversations(since, args.conv_id, args.tenant)
    if args.conv_id and not convs:
        print(f"conv {args.conv_id} not found in tenant {args.tenant}", file=sys.stderr)
        sys.exit(1)

    convs = convs[: args.limit]
    log.info("consolidate.start", n_conversations=len(convs),
              tenant=args.tenant, since_hours=args.since_hours,
              dry_run=args.dry_run)

    totals = {"convs": 0, "decisions": 0, "actions": 0, "skipped": 0}
    for c in convs:
        try:
            r = await consolidate_conv(c, dry_run=args.dry_run)
        except Exception as e:
            log.exception("consolidate.conv.error", conv_id=c["id"])
            r = {"status": "error", "err": str(e)[:200]}
        totals["convs"] += 1
        if r.get("status") in ("ok", "dry_run"):
            totals["decisions"] += r.get("decisions", 0)
            totals["actions"] += r.get("actions", 0)
        elif r.get("status") == "already":
            totals["skipped"] += 1
        if args.dry_run and r.get("preview"):
            print(f"\n=== {c.get('title','(no title)')[:60]} ===")
            print(json.dumps(r["preview"], ensure_ascii=False, indent=2)[:600])

    print(json.dumps({"summary": totals}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
