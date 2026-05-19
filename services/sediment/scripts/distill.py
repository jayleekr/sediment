"""Digest "정리" agent — closes the two structural breaks in the internal
dogfood loop (plan 2026-05-19 Diagram 3; the GATE-A lever + the data-
refinement moat prototype).

Two breaks this fixes:
  1. INPUT  — consolidate_memory.py only reads `conversations`; the highest-
     value source (Discord #weekly Gemini meeting notes, captured into
     `events` by discord_ingest) was NEVER distilled.
  2. OUTPUT — distilled decisions land only in the `decisions` table, but RAG
     retrieval reads ONLY chunks⨝artifacts. So "왜 이렇게 결정했나" was
     structurally unanswerable. This lands each decision as a CITABLE vault
     artifact (chunked+embedded via the existing ingest endpoint) and links
     it back via decisions.source_artifact_id (column already in the schema).

Reuses, does not reinvent: the Anthropic tool-use extractor + dedup inserts
from consolidate_memory.py, and the /v1/ingest/document pipeline.

Run:
    .venv/bin/python -m scripts.distill                 # conversations + events → vault
    .venv/bin/python -m scripts.distill --dry-run       # write artifacts to a dir, no DB/LLM/ingest
    .venv/bin/python -m scripts.distill --since-hours 168
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import json
import re
import sys
from pathlib import Path

import httpx
from sqlalchemy import text

from lab_lib.db import service_session
from lab_lib.logging import configure_logging, get_logger
from lab_lib.settings import settings
from scripts.consolidate_memory import (
    _EXTRACT_TOOL,  # noqa: F401  (kept for parity / future direct use)
    _SYSTEM,        # noqa: F401
    _extract,
    _insert_action,
    _insert_decision,
    _resolve_owner,
)

configure_logging()
log = get_logger("distill")

INGESTER_URL = f"http://localhost:{settings.vault_ingester_port}/v1/ingest/document"

# Highest-value first. #잡담 is intentionally excluded (noise — plan Diagram 3).
GOOD_DISCORD_CHANNELS = {
    "weekly", "daily-research", "인사이트-공유", "content-pipeline",
}
DRY_DIR = Path(__file__).resolve().parents[3] / "output" / "dogfood" / "distilled"


def _slug(s: str) -> str:
    s = re.sub(r"\s+", "-", s.strip().lower())
    s = re.sub(r"[^0-9a-z가-힣\-]", "", s)
    return (s[:60] or "decision").strip("-")


async def _default_tenant_id() -> str | None:
    async with service_session() as s:
        r = await s.execute(text("SELECT id::text FROM tenants WHERE slug = :sl"),
                             {"sl": settings.default_tenant_slug})
        row = r.first()
        return row[0] if row else None


async def _conversation_sources(tid: str, since: _dt.datetime) -> list[dict]:
    """Existing path: recent conversations as transcripts."""
    async with service_session() as s:
        cr = await s.execute(text("""
            SELECT id::text, COALESCE(title,'(untitled)')
            FROM conversations
            WHERE tenant_id = :tid AND updated_at >= :since
            ORDER BY updated_at DESC LIMIT 50
        """), {"tid": tid, "since": since})
        convs = [(row[0], row[1]) for row in cr]
        out = []
        for cid, title in convs:
            mr = await s.execute(text("""
                SELECT role, content FROM messages
                WHERE conv_id = :cid AND role IN ('user','assistant')
                ORDER BY ts ASC
            """), {"cid": cid})
            msgs = [{"role": m[0], "content": m[1]} for m in mr]
            if msgs:
                out.append({"src": f"conv/{cid}", "title": title, "messages": msgs})
        return out


async def _event_sources(tid: str, since: _dt.datetime) -> list[dict]:
    """NEW path: high-value Discord captures grouped per (channel, day) into a
    transcript. This is the input break that was never wired."""
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT payload, ts FROM events
            WHERE tenant_id = :tid AND source = 'discord' AND ts >= :since
            ORDER BY ts ASC
        """), {"tid": tid, "since": since})
        rows = [(row[0], row[1]) for row in r]
    groups: dict[tuple[str, str], list[str]] = {}
    for payload, ts in rows:
        p = payload if isinstance(payload, dict) else json.loads(payload or "{}")
        ch = str(p.get("channel") or p.get("channel_name") or "").lstrip("#")
        if ch not in GOOD_DISCORD_CHANNELS:
            continue
        day = (ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10])
        who = p.get("author") or p.get("user") or p.get("author_name") or "?"
        content = (p.get("content") or p.get("text") or "").strip()
        if content:
            groups.setdefault((ch, day), []).append(f"[{who}] {content}")
    out = []
    for (ch, day), lines in groups.items():
        out.append({
            "src": f"discord/{ch}/{day}",
            "title": f"#{ch} {day}",
            "messages": [{"role": "user", "content": "\n".join(lines)}],
        })
    return out


def _decision_markdown(d: dict, src: str, title: str) -> tuple[str, str]:
    """Build a citable vault artifact for one decision. ref is topic-slugged
    so re-distilling the same decision UPDATES (vault-differ: known/update),
    never duplicates."""
    topic = (d.get("topic") or "decision").strip()
    ref = f"decision/{_slug(topic)}"
    today = _dt.date.today().isoformat()
    fm = {
        "type": "decision",
        "topic": topic,
        "status": d.get("status") or "made",
        "date": today,
        "slug": _slug(topic),
        "source": src,
        "source_title": title,
    }
    fm_block = "\n".join(f'{k}: "{v}"' for k, v in fm.items())
    body = (
        f"---\n{fm_block}\n---\n\n"
        f"# {topic}\n\n"
        f"**결정(왜):** {d.get('body','').strip()}\n\n"
        f"_출처: {title} ({src})_\n"
    )
    return ref, body


async def _ingest_artifact(client: httpx.AsyncClient, tid: str, ref: str,
                            body: str) -> str | None:
    try:
        r = await client.post(INGESTER_URL, timeout=120, json={
            "tenant_id": tid, "ref": ref, "type": "decision", "body": body,
        })
        if r.status_code == 200:
            return r.json().get("artifact_id")
        log.warning("distill.ingest.fail", ref=ref, status=r.status_code)
    except Exception as e:
        log.warning("distill.ingest.err", ref=ref, err=str(e))
    return None


async def _link_source_artifact(decision_id: str, artifact_id: str) -> None:
    async with service_session() as s:
        await s.execute(text(
            "UPDATE decisions SET source_artifact_id = :aid WHERE id = :did"
        ), {"aid": artifact_id, "did": decision_id})
        await s.commit()


async def run(since_hours: int, dry_run: bool) -> dict:
    since = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=since_hours)
    summary = {"sources": 0, "decisions": 0, "artifacts": 0, "actions": 0,
               "dry_run": dry_run, "flags": []}

    if dry_run:
        # Genuinely offline: NO DB, NO LLM, NO ingest. Exercise the pure
        # artifact-shaping logic (_decision_markdown + _slug + ref idempotency
        # + DRY_DIR write) on a synthetic fixture so the runbook's "proves
        # logic, no DB/LLM needed" claim is actually true.
        DRY_DIR.mkdir(parents=True, exist_ok=True)
        fixture = [{
            "src": "discord/weekly/2026-05-19",
            "title": "#weekly 2026-05-19 (dry-run synthetic fixture)",
            "decisions": [
                {"topic": "Sediment 레포 분리",
                 "body": "AX 컨설팅 SaaS 경로 — 커뮤니티 모노레포에서 독립.",
                 "status": "made"},
                {"topic": "강의용 Studio는 해자가 아님",
                 "body": "Cursor류 도구·연료 수집용. 해자는 Sediment 정제 능력.",
                 "status": "made"},
            ],
        }]
        for f in fixture:
            summary["sources"] += 1
            for d in f["decisions"]:
                ref, md = _decision_markdown(d, f["src"], f["title"])
                (DRY_DIR / f"{_slug(d['topic'])}.md").write_text(md)
                summary["decisions"] += 1
        summary["dry_run_note"] = (
            f"OFFLINE: synthetic fixture only. Wrote {summary['decisions']} "
            f"decision .md to {DRY_DIR}. NO DB/LLM/ingest touched; nothing "
            "persisted to the vault. Counts are would-be, not stored."
        )
        summary["flags"].append(
            "dry-run: synthetic fixture; live run needs DB + ANTHROPIC key + "
            "the vault_ingester running")
        return summary

    try:
        tid = await _default_tenant_id()
    except Exception as e:
        summary["flags"].append(f"DB unreachable: {e} — nothing distilled")
        return summary
    if not tid:
        summary["flags"].append("default tenant not found — run `make seed`")
        return summary

    sources = (await _conversation_sources(tid, since)) + (await _event_sources(tid, since))
    summary["sources"] = len(sources)
    if not sources:
        summary["flags"].append("no conversations or Discord captures in window")
        return summary

    have_llm = bool(settings.anthropic_api_key) and settings.anthropic_api_key != "sk-ant-..."
    if not have_llm:
        summary["flags"].append(
            "ANTHROPIC_API_KEY not configured — extraction skipped (honest no-op, "
            "not a fake pass). Borrowed Sonatus key must NOT be used in prod."
        )
        return summary

    async with httpx.AsyncClient() as client:
        for s in sources:
            try:
                extracted = await _extract(s["messages"])
            except Exception as e:
                summary["flags"].append(f"extract failed for {s['src']}: {e}")
                continue
            decisions = extracted.get("decisions") or []
            actions = extracted.get("actions") or []
            topic_to_did: dict[str, str] = {}
            for d in decisions:
                ref, md = _decision_markdown(d, s["src"], s["title"])
                summary["decisions"] += 1
                # conv_id only when the source is a conversation (events → NULL,
                # _insert_decision handles the IS NULL dedup).
                conv_id = s["src"].split("/", 1)[1] if s["src"].startswith("conv/") else None
                did = await _insert_decision(
                    tid, conv_id, d.get("topic", ""), d.get("body", ""),
                    d.get("status") or "made",
                )
                if did:
                    topic_to_did[d.get("topic", "")] = did
                aid = await _ingest_artifact(client, tid, ref, md)
                if aid:
                    summary["artifacts"] += 1
                    if did:
                        await _link_source_artifact(did, aid)
                else:
                    # BLOCK-1: never let a dropped artifact be silent — the
                    # whole point is RAG-citable decisions.
                    summary["flags"].append(
                        f"artifact ingest FAILED (decision not citable): "
                        f"{d.get('topic','?')!r} — is vault_ingester running?")
            for a in actions:
                owner_id = await _resolve_owner(tid, a.get("owner_hint"))
                did = topic_to_did.get(a.get("decision_topic") or "")
                await _insert_action(tid, did, owner_id, a.get("description", ""),
                                     a.get("due_date"))
                summary["actions"] += 1
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since-hours", type=int, default=168)  # 7 days
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    out = asyncio.run(run(args.since_hours, args.dry_run))
    json.dump(out, sys.stdout, indent=2, default=str)
    print()
    # exit non-zero only on a hard config gap, so cron alerts but dry-runs pass
    sys.exit(0)


if __name__ == "__main__":
    main()
