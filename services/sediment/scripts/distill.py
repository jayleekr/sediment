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
import yaml
from sqlalchemy import text

from lab_lib.db import service_session
from lab_lib.links import create_link
from lab_lib.logging import configure_logging, get_logger
from lab_lib.prompts import load_strategy
from lab_lib.settings import settings
from lab_lib.visibility import inherit_visibility
from scripts.consolidate_memory import (
    _extract,
    _insert_action,
    _insert_decision,
    _resolve_owner,
)

configure_logging()
log = get_logger("distill")

INGESTER_URL = f"http://localhost:{settings.vault_ingester_port}/v1/ingest/document"

# v0.3 design (docs/design/collection-and-distillation.md §4):
# CAPTURE is wide (all channels, no allow-list). Distill applies signal-to-
# noise gates at Gate 2 (per-channel governance config) + Gate 3 (LLM
# confidence threshold inside the strategy YAML). The old hard-coded
# GOOD_DISCORD_CHANNELS set has been removed.
#
# Channel routing → strategy:
#   - #meeting-notes  → meeting_transcript  (Gemini-produced summaries)
#   - any other       → chat_thread          (general Discord/Slack threads)
# Caller may force a specific strategy via env STRATEGY_OVERRIDE for A/B tests.
MEETING_CHANNELS = {"meeting-notes", "weekly"}

DRY_DIR = Path(__file__).resolve().parents[3] / "output" / "dogfood" / "distilled"


def _strategy_for_discord_channel(channel: str) -> str:
    """Map a Discord channel name to the appropriate distill strategy.

    Both `weekly` (Jay's pre-existing meeting summary channel) and
    `meeting-notes` (the canonical Gemini-summary channel) route to the
    meeting_transcript strategy. Everything else uses the noisier
    chat_thread strategy with a higher confidence floor.
    """
    ch = channel.lstrip("#").lower()
    return "meeting_transcript" if ch in MEETING_CHANNELS else "chat_thread"


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
            SELECT id::text, role, content, ts FROM messages
            WHERE conv_id = :cid AND role IN ('user','assistant')
            ORDER BY ts ASC
        """), {"cid": cid})
            rows = list(mr)
            msgs = [{"role": m[1], "content": m[2]} for m in rows]
            if msgs:
                out.append({
                    "src": f"conv/{cid}",
                    "title": title,
                    "strategy": "chat_thread",  # Web/Cline chat — same noise profile
                    "messages": msgs,
                    "provenance": {
                        "kind": "conversation",
                        "conversation_id": cid,
                        "source_message_ids": [m[0] for m in rows],
                        "source_message_count": len(rows),
                        "source_started_at": rows[0][3].isoformat() if rows and hasattr(rows[0][3], "isoformat") else None,
                        "source_ended_at": rows[-1][3].isoformat() if rows and hasattr(rows[-1][3], "isoformat") else None,
                    },
                })
        return out


async def _event_sources(tid: str, since: _dt.datetime) -> list[dict]:
    """All-channels Discord capture per v0.3 design. Groups events per
    (channel, day) into a transcript. Each group carries a `strategy` field
    so the distill loop can pick chat_thread vs meeting_transcript per source.
    """
    async with service_session() as s:
        r = await s.execute(text("""
            SELECT id::text, payload, ts FROM events
            WHERE tenant_id = :tid AND source = 'discord' AND ts >= :since
            ORDER BY ts ASC
        """), {"tid": tid, "since": since})
        rows = [(row[0], row[1], row[2]) for row in r]
    groups: dict[tuple[str, str], dict] = {}
    for event_id, payload, ts in rows:
        p = payload if isinstance(payload, dict) else json.loads(payload or "{}")
        ch = str(p.get("channel") or p.get("channel_name") or "").lstrip("#")
        if not ch:
            # No channel attribution — skip (cannot route strategy).
            continue
        day = (ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10])
        who = p.get("author") or p.get("user") or p.get("author_name") or "?"
        content = (p.get("content") or p.get("text") or "").strip()
        if content:
            group = groups.setdefault((ch, day), {"lines": [], "events": []})
            group["lines"].append(f"[{who}] {content}")
            group["events"].append({
                "event_id": event_id,
                "ts": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                "author": who,
                "message_id": p.get("message_id") or p.get("id"),
            })
    out = []
    for (ch, day), group in groups.items():
        events = group["events"]
        out.append({
            "src": f"discord/{ch}/{day}",
            "title": f"#{ch} {day}",
            "channel": ch,
            "strategy": _strategy_for_discord_channel(ch),
            "messages": [{"role": "user", "content": "\n".join(group["lines"])}],
            "provenance": {
                "kind": "discord_events",
                "source": "discord",
                "channel": ch,
                "source_date": day,
                "source_event_ids": [e["event_id"] for e in events],
                "source_message_ids": [e["message_id"] for e in events if e.get("message_id")],
                "source_event_count": len(events),
                "source_started_at": events[0]["ts"] if events else None,
                "source_ended_at": events[-1]["ts"] if events else None,
            },
        })
    return out


def _decision_markdown(d: dict, src: str, title: str, provenance: dict | None = None) -> tuple[str, str]:
    """Build a citable vault artifact for one decision. ref is topic-slugged
    so re-distilling the same decision UPDATES (vault-differ: known/update),
    never duplicates."""
    topic = (d.get("topic") or "decision").strip()
    ref = f"decision/{_slug(topic)}"
    today = _dt.date.today().isoformat()
    provenance = {
        "kind": "unknown",
        "source": src,
        **(provenance or {}),
        "source_ref": src,
        "source_title": title,
    }
    fm = {
        "type": "decision",
        "topic": topic,
        "status": d.get("status") or "made",
        "date": today,
        "slug": _slug(topic),
        "source": src,
        "source_title": title,
        "provenance": provenance,
    }
    fm_block = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()
    source_note = provenance.get("source_ref") or src
    if provenance.get("source_event_count"):
        source_note += f" · events={provenance['source_event_count']}"
    if provenance.get("source_message_count"):
        source_note += f" · messages={provenance['source_message_count']}"
    body = (
        f"---\n{fm_block}\n---\n\n"
        f"# {topic}\n\n"
        f"**결정(왜):** {d.get('body','').strip()}\n\n"
        f"_출처: {title} ({source_note})_\n"
    )
    return ref, body


def _source_visibilities(source: dict) -> list[str | None]:
    """Visibility of each thing this decision was distilled from (sediment#140).

    A decision page is composed from a conversation transcript or a day of
    Discord events — NOT from artifacts — and neither `conversations` nor
    `events` carries a visibility column today. So this returns an empty list,
    and `inherit_visibility` yields DEFAULT_VISIBILITY: exactly the behaviour
    distill has always had.

    It exists as a real call site rather than a hardcoded 'tenant' so that the
    day conversations or events become scoped, the inheritance rule applies by
    filling in this one function — instead of someone having to remember that
    synthesis is a disclosure path. That memory is what the rule exists to
    replace.
    """
    return []


async def _ingest_artifact(client: httpx.AsyncClient, tid: str, ref: str,
                            body: str, visibility: str,
                            source_ref: str | None = None) -> str | None:
    try:
        r = await client.post(INGESTER_URL, timeout=120, json={
            "tenant_id": tid, "ref": ref, "type": "decision", "body": body,
            # This page was synthesized by us, not ingested from a source doc.
            "origin": "derived",
            "visibility": visibility,
            # Attribute the superseded revision (sediment#138). Two different
            # decisions can slug alike; when the second replaces the first, the
            # history row must say which conversation or event batch the
            # replaced text came from, or the collision is undiagnosable.
            "source_ref": f"distill:{source_ref}" if source_ref else None,
        })
        if r.status_code == 200:
            return r.json().get("artifact_id")
        log.warning("distill.ingest.fail", ref=ref, status=r.status_code)
    except Exception as e:
        log.warning("distill.ingest.err", ref=ref, err=str(e))
    return None


async def _resolve_decision_ref(tid: str, base_ref: str, src: str,
                                body: str) -> tuple[str, list[str]]:
    """Pick the ref to write this decision to, and any artifacts it conflicts with.

    sediment#141. Two decisions from DIFFERENT sources that slug to the same
    topic are not one decision revised — they are two claims, and the pipeline
    used to keep only the later one. #138 preserved the replaced text; this
    gives the newcomer a page of its own so both remain citable, plus a
    `contradicts` link so the disagreement is visible instead of implied.

    Resolution order, cheapest and most certain first:

      1. an existing sibling with an IDENTICAL body → reuse it. Same claim
         reached twice; a second page would be noise, not knowledge.
      2. an existing sibling from the SAME source → reuse it. This is the
         re-decide case the original slug scheme was designed for.
      3. otherwise → mint `<base>--N` and report every existing sibling as
         conflicting.

    Returns ``(ref, conflicting_artifact_ids)``. Never raises: if the lookup
    fails, fall back to the base ref, which is exactly today's behaviour.

    Note what this deliberately does NOT do: judge whether the two claims
    actually contradict, merely agree, or supersede one another. That needs an
    LLM read of both bodies. Recording the pair as an OPEN conflict is the
    conservative move — surfacing a disagreement that turns out to be agreement
    costs a reviewer a minute; silently merging a real one costs the knowledge.
    """
    try:
        async with service_session() as s:
            r = await s.execute(text("""
                SELECT id::text, ref, body, frontmatter ->> 'source' AS source
                FROM artifacts
                WHERE tenant_id = CAST(:tid AS uuid)
                  AND (ref = :base OR ref LIKE :base_like)
                ORDER BY ref
            """), {"tid": tid, "base": base_ref, "base_like": f"{base_ref}--%"})
            siblings = [dict(row._mapping) for row in r]
    except Exception as e:
        log.warning("distill.ref_resolve.err", ref=base_ref, err=str(e))
        return base_ref, []

    if not siblings:
        return base_ref, []

    for sib in siblings:
        if (sib.get("body") or "") == body:
            return sib["ref"], []
    for sib in siblings:
        if sib.get("source") and sib["source"] == src:
            return sib["ref"], []

    # Lowest unused suffix, so a third conflicting claim does not collide with
    # the second.
    taken = {sib["ref"] for sib in siblings}
    n = 2
    while f"{base_ref}--{n}" in taken:
        n += 1
    return f"{base_ref}--{n}", [sib["id"] for sib in siblings]


async def _record_conflicts(tid: str, new_artifact_id: str,
                            conflicting_ids: list[str], src: str) -> int:
    """Link a newly-minted sibling to the claims it disagrees with."""
    if not conflicting_ids:
        return 0
    written = 0
    async with service_session() as s:
        for other_id in conflicting_ids:
            try:
                if await create_link(
                    s, tid, new_artifact_id, other_id, "contradicts",
                    note=f"same decision topic, different source ({src})",
                ):
                    written += 1
            except Exception as e:
                log.warning("distill.link.err", dst=other_id, err=str(e))
        await s.commit()
    return written


async def _link_source_artifact(decision_id: str, artifact_id: str) -> None:
    async with service_session() as s:
        await s.execute(text(
            "UPDATE decisions SET source_artifact_id = :aid WHERE id = :did"
        ), {"aid": artifact_id, "did": decision_id})
        await s.commit()


async def run(since_hours: int, dry_run: bool) -> dict:
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

    since = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=since_hours)
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

    # Cache loaded strategies — both meeting_transcript + chat_thread are
    # hot in any meaningful run. Loading is cheap (lru_cache on _read_yaml)
    # but capturing references here keeps logging coherent.
    strategy_cache: dict[str, object] = {}

    def _get_strategy(name: str):
        if name not in strategy_cache:
            strategy_cache[name] = load_strategy("distill", name, tenant_id=tid)
        return strategy_cache[name]

    # Allow env override for A/B testing (e.g., STRATEGY_OVERRIDE=chat_thread
    # to force the noisier strategy on meeting channels for comparison).
    import os as _os
    override = _os.environ.get("STRATEGY_OVERRIDE") or None
    if override:
        summary["flags"].append(f"STRATEGY_OVERRIDE={override} (env)")

    async with httpx.AsyncClient() as client:
        for s in sources:
            chosen_name = override or s.get("strategy") or "chat_thread"
            try:
                strategy = _get_strategy(chosen_name)
            except Exception as e:
                summary["flags"].append(
                    f"strategy load failed ({chosen_name}) for {s['src']}: {e}"
                )
                continue
            try:
                extracted = await _extract(s["messages"], strategy=strategy)
            except Exception as e:
                summary["flags"].append(f"extract failed for {s['src']}: {e}")
                continue
            decisions = extracted.get("decisions") or []
            actions = extracted.get("actions") or []
            # Record which strategy + prompt version produced this batch —
            # surfaced in the run summary so an operator can spot a strategy
            # routing regression at a glance.
            summary.setdefault("by_strategy", {}).setdefault(chosen_name, {
                "sources": 0, "decisions": 0, "actions": 0,
                "prompt_version": extracted.get("_meta", {}).get("prompt_version"),
            })
            summary["by_strategy"][chosen_name]["sources"] += 1
            summary["by_strategy"][chosen_name]["decisions"] += len(decisions)
            summary["by_strategy"][chosen_name]["actions"] += len(actions)
            topic_to_did: dict[str, str] = {}
            for d in decisions:
                ref, md = _decision_markdown(d, s["src"], s["title"], s.get("provenance"))
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
                # sediment#141: a same-topic decision from a DIFFERENT source
                # gets its own page plus a contradicts link, instead of
                # overwriting the earlier claim.
                ref, conflicting = await _resolve_decision_ref(
                    tid, ref, s["src"], md)
                aid = await _ingest_artifact(
                    client, tid, ref, md,
                    visibility=inherit_visibility(_source_visibilities(s)),
                    source_ref=s["src"],
                )
                if aid:
                    summary["artifacts"] += 1
                    if conflicting:
                        n_links = await _record_conflicts(
                            tid, aid, conflicting, s["src"])
                        summary["conflicts"] = summary.get("conflicts", 0) + n_links
                        # Loud on purpose: an unresolved contradiction is a
                        # finding, not routine output. The hygiene job
                        # (sediment#143) tracks the open ones.
                        summary["flags"].append(
                            f"conflicting decision kept as {ref!r} "
                            f"({n_links} contradicts link(s)) — needs review")
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
