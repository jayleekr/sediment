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
from lab_lib.entities import (
    entity_markdown,
    extract_entities,
    learn_aliases,
    link_mention,
)
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


def _source_artifact_markdown(source: dict) -> tuple[str, str, str]:
    """Build a citable artifact for a captured transcript (sediment#161).

    Capture is wide by design — all channels, no allow-list (v0.3 §4) — but
    retrieval reads only chunks⨝artifacts, and the only thing distill ever
    wrote back was decisions. So everything the LLM did not classify as a
    decision was captured and then unreachable: not searchable, not citable,
    and not linkable as evidence for the decisions drawn from it.

    `ref` is the group's own `src` ("discord/<channel>/<YYYY-MM-DD>"), which is
    already stable and unique per (channel, day), so re-running distill updates
    the day's transcript in place rather than duplicating it.

    origin='raw': this is captured source text, not something we synthesized.
    That matters for #140's layering and for #143's hygiene metrics, both of
    which treat 'derived' as "Sediment wrote this".
    """
    src = source["src"]
    prov = dict(source.get("provenance") or {})
    fm = {
        "type": "message",
        "title": source.get("title") or src,
        "source": prov.get("source") or "discord",
        "channel": source.get("channel"),
        "date": prov.get("source_date"),
        "slug": _slug(f"{source.get('channel') or 'chat'}-{prov.get('source_date') or ''}"),
        "provenance": prov,
    }
    fm_block = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()
    transcript = "\n\n".join(
        m.get("content", "") for m in source.get("messages") or []
    ).strip()
    # `content` is what the ingester will store in artifacts.body — it strips
    # the frontmatter before writing. Returned separately so the unchanged
    # check compares like with like instead of re-parsing the assembled string.
    content = f"# {fm['title']}\n\n{transcript}\n"
    body = f"---\n{fm_block}\n---\n\n{content}"
    return src, body, content


async def _existing_artifact_body(tid: str, ref: str) -> tuple[str, str, int] | None:
    """(artifact_id, body, rev) for `ref`, or None. Used to skip unchanged
    re-ingest, and to arm the optimistic lock on the write that follows.

    A (channel, day) group is re-derived on every run while that day is still
    live, and each ingest deletes and re-embeds every chunk. Skipping identical
    bodies keeps the embedding cost proportional to new conversation rather
    than to how often the scheduler fires.
    """
    try:
        async with service_session() as s:
            r = await s.execute(text("""
                SELECT id::text, body, rev FROM artifacts
                WHERE tenant_id = CAST(:tid AS uuid) AND ref = :ref
            """), {"tid": tid, "ref": ref})
            row = r.first()
            return (row[0], row[1] or "", int(row[2])) if row else None
    except Exception as e:
        log.warning("distill.source_artifact.lookup_err", ref=ref, err=str(e)[:200])
        return None


async def _ingest_source_artifacts(client: httpx.AsyncClient, tid: str,
                                   sources: list[dict], summary: dict) -> dict[str, str]:
    """Land each captured transcript as an artifact. Returns {src: artifact_id}.

    Only event captures (Discord). Conversations are deliberately excluded:
    `messages` already stores them, they are per-member rather than shared, and
    publishing chat logs into the shared vault is a visibility decision (#140)
    that belongs with whoever makes the policy, not with this batch job.
    """
    mapping: dict[str, str] = {}
    for source in sources:
        prov = source.get("provenance") or {}
        if prov.get("kind") != "discord_events":
            continue
        ref, body, content = _source_artifact_markdown(source)

        existing = await _existing_artifact_body(tid, ref)
        if existing and existing[1].strip() == content.strip():
            # Body identical to what is stored — nothing new was said in this
            # (channel, day) since the last run.
            mapping[source["src"]] = existing[0]
            summary["source_artifacts_unchanged"] = summary.get(
                "source_artifacts_unchanged", 0) + 1
            continue

        try:
            aid = await _ingest_artifact(
                client, tid, ref, body,
                visibility=inherit_visibility(_source_visibilities(source)),
                source_ref=source["src"], artifact_type="message", origin="raw",
                # existing[2] is the rev read in the same lookup that decided
                # this body had changed; None when the day is new.
                expected_rev=existing[2] if existing else None,
            )
        except RevConflict as e:
            summary["rev_conflicts"] = summary.get("rev_conflicts", 0) + 1
            summary["flags"].append(
                f"rev conflict — transcript {ref!r} skipped, another writer "
                f"updated it mid-run ({e})")
            continue
        if aid:
            mapping[source["src"]] = aid
            summary["source_artifacts"] = summary.get("source_artifacts", 0) + 1
        else:
            summary["flags"].append(
                f"transcript ingest FAILED (capture not citable): {ref!r} "
                "— is vault_ingester running?")
    return mapping


async def _process_entities(client: httpx.AsyncClient, tid: str, source: dict,
                            source_artifact_id: str | None, summary: dict) -> None:
    """Extract entities from one source and wire them into the graph (#168).

    Requires a landed source artifact: a `mentions` link needs something to
    point FROM, and that is exactly what #161 created. Sources without one
    (conversations, which are deliberately not published to the shared vault)
    are skipped rather than half-processed.

    Never raises. This is an enrichment tail — the decisions and transcripts
    produced earlier in the run must survive its failure.
    """
    if not source_artifact_id:
        return
    transcript = "\n\n".join(
        m.get("content", "") for m in source.get("messages") or []
    ).strip()
    try:
        entities = await extract_entities(transcript, tenant_id=tid)
    except Exception as e:
        summary["flags"].append(f"entity extraction failed for {source['src']}: {e}")
        return

    for ent in entities:
        ref, body = entity_markdown(ent)
        # No expected_rev here, deliberately (sediment#162). An entity page is a
        # convergent upsert: every writer produces the same page from the same
        # entity, so there is no per-source content for a racing writer to
        # destroy. Arming the lock would only manufacture conflicts between two
        # sources that happen to mention the same project.
        aid = await _ingest_artifact(
            client, tid, ref, body,
            visibility=inherit_visibility(_source_visibilities(source)),
            source_ref=source["src"], artifact_type="entity", origin="derived",
        )
        if not aid:
            summary["flags"].append(f"entity page ingest FAILED: {ref!r}")
            continue
        summary["entity_pages"] = summary.get("entity_pages", 0) + 1
        try:
            async with service_session() as s:
                if await link_mention(s, tid, source_artifact_id, aid):
                    summary["entity_mentions"] = summary.get("entity_mentions", 0) + 1
                summary["entity_aliases"] = (
                    summary.get("entity_aliases", 0) + await learn_aliases(s, tid, ent))
                await s.commit()
        except Exception as e:
            log.warning("distill.entity_wiring.err", ref=ref, err=str(e)[:200])


async def _link_decision_to_source(tid: str, decision_artifact_id: str,
                                   source_artifact_id: str, src: str) -> bool:
    """Record that a decision page was drawn from a captured transcript.

    This is the first `derived_from` edge in the system that points at real
    captured source text. #143's stale_derived metric has been reporting "not
    yet meaningful" precisely because no such edge existed.
    """
    if not decision_artifact_id or not source_artifact_id:
        return False
    try:
        async with service_session() as s:
            created = await create_link(
                s, tid, decision_artifact_id, source_artifact_id, "derived_from",
                note=f"distilled from {src}",
            )
            await s.commit()
            return bool(created)
    except Exception as e:
        log.warning("distill.derived_from.err", src=src, err=str(e)[:200])
        return False


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


class RevConflict(Exception):
    """Someone else wrote this artifact between our read and our write.

    sediment#162. The ingester has been able to detect this since #138, but
    nothing passed `expected_rev`, so every writer was last-writer-wins and a
    lost update was invisible. Raised rather than folded into the None return
    so callers cannot treat "somebody else got here first" as "the ingester is
    down" — those need opposite responses.
    """


async def _ingest_artifact(client: httpx.AsyncClient, tid: str, ref: str,
                            body: str, visibility: str,
                            source_ref: str | None = None,
                            artifact_type: str = "decision",
                            origin: str = "derived",
                            expected_rev: int | None = None) -> str | None:
    """POST one artifact to the ingester.

    Defaults describe a distilled decision — synthesized by us. sediment#161
    added the other caller: a captured transcript, which is `message`/`raw`
    because we did not write it, we only stored it.

    `expected_rev` opts this write into the optimistic lock (sediment#162);
    a mismatch raises RevConflict. Pass None only when the ref is new or when
    last-writer-wins is genuinely correct.
    """
    try:
        r = await client.post(INGESTER_URL, timeout=120, json={
            "expected_rev": expected_rev,
            "tenant_id": tid, "ref": ref, "type": artifact_type, "body": body,
            "origin": origin,
            "visibility": visibility,
            # Attribute the superseded revision (sediment#138). Two different
            # decisions can slug alike; when the second replaces the first, the
            # history row must say which conversation or event batch the
            # replaced text came from, or the collision is undiagnosable.
            "source_ref": f"distill:{source_ref}" if source_ref else None,
        })
        if r.status_code == 200:
            return r.json().get("artifact_id")
        if r.status_code == 409:
            raise RevConflict(f"{ref}: {r.text[:200]}")
        log.warning("distill.ingest.fail", ref=ref, status=r.status_code)
    except RevConflict:
        raise
    except Exception as e:
        log.warning("distill.ingest.err", ref=ref, err=str(e))
    return None


async def _resolve_decision_ref(tid: str, base_ref: str, src: str,
                                body: str) -> tuple[str, list[str], int | None]:
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

    Returns ``(ref, conflicting_artifact_ids, expected_rev)``. ``expected_rev``
    is the chosen artifact's current rev, or None when the ref is new — this
    lookup already reads the row, so passing it on costs nothing and is what
    makes the optimistic lock #138 built actually fire (sediment#162).

    Never raises: if the lookup fails, fall back to the base ref with no
    expected_rev, which is exactly today's behaviour.

    Note what this deliberately does NOT do: judge whether the two claims
    actually contradict, merely agree, or supersede one another. That needs an
    LLM read of both bodies. Recording the pair as an OPEN conflict is the
    conservative move — surfacing a disagreement that turns out to be agreement
    costs a reviewer a minute; silently merging a real one costs the knowledge.
    """
    try:
        async with service_session() as s:
            r = await s.execute(text("""
                SELECT id::text, ref, body, rev,
                       frontmatter ->> 'source' AS source
                FROM artifacts
                WHERE tenant_id = CAST(:tid AS uuid)
                  AND (ref = :base OR ref LIKE :base_like)
                ORDER BY ref
            """), {"tid": tid, "base": base_ref, "base_like": f"{base_ref}--%"})
            siblings = [dict(row._mapping) for row in r]
    except Exception as e:
        log.warning("distill.ref_resolve.err", ref=base_ref, err=str(e))
        return base_ref, [], None

    if not siblings:
        return base_ref, [], None

    for sib in siblings:
        if (sib.get("body") or "") == body:
            return sib["ref"], [], sib.get("rev")
    for sib in siblings:
        if sib.get("source") and sib["source"] == src:
            return sib["ref"], [], sib.get("rev")

    # Lowest unused suffix, so a third conflicting claim does not collide with
    # the second.
    taken = {sib["ref"] for sib in siblings}
    n = 2
    while f"{base_ref}--{n}" in taken:
        n += 1
    # A freshly minted sibling ref has no existing row, so there is nothing to
    # race on — expected_rev is None by construction, not by omission.
    return f"{base_ref}--{n}", [sib["id"] for sib in siblings], None


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
               # sediment#161 — captured transcripts landed as artifacts, how
               # many were already up to date, and how many decision pages now
               # point back at the capture they came from.
               "source_artifacts": 0, "source_artifacts_unchanged": 0,
               "evidence_links": 0,
               # sediment#168 — entity pages, mentions links, learned aliases.
               "entity_pages": 0, "entity_mentions": 0, "entity_aliases": 0,
               # sediment#162 — writes skipped because someone else won the race.
               "rev_conflicts": 0,
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

    # sediment#161: land the captured transcripts FIRST, and deliberately
    # before the LLM gate below. Making a Discord conversation searchable is
    # storage, not synthesis — it needs no model. Doing it after the gate would
    # mean a tenant without an Anthropic key captures everything and can
    # retrieve none of it, which is the exact failure this issue is about.
    source_artifact_ids: dict[str, str] = {}
    if not dry_run:
        async with httpx.AsyncClient() as client:
            source_artifact_ids = await _ingest_source_artifacts(
                client, tid, sources, summary)

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
                ref, conflicting, expected_rev = await _resolve_decision_ref(
                    tid, ref, s["src"], md)
                try:
                    aid = await _ingest_artifact(
                        client, tid, ref, md,
                        visibility=inherit_visibility(_source_visibilities(s)),
                        source_ref=s["src"], expected_rev=expected_rev,
                    )
                except RevConflict as e:
                    # Batch job: skip this decision and say so. Retrying blind
                    # would overwrite whoever won the race — the exact loss
                    # #138 stopped. The next scheduled run re-reads and
                    # re-resolves, and #141 already handles the case where the
                    # other writer produced a genuinely different claim.
                    summary["rev_conflicts"] = summary.get("rev_conflicts", 0) + 1
                    summary["flags"].append(
                        f"rev conflict — decision {ref!r} skipped, another "
                        f"writer updated it mid-run ({e})")
                    continue
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
                    # sediment#161: point the decision page at the transcript it
                    # was drawn from. Before this there was nothing to point at —
                    # the capture existed only as `events` rows, which retrieval
                    # never reads.
                    src_aid = source_artifact_ids.get(s["src"])
                    if src_aid and await _link_decision_to_source(
                            tid, aid, src_aid, s["src"]):
                        summary["evidence_links"] = summary.get("evidence_links", 0) + 1
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
            # sediment#168 — last, and deliberately so. Entity extraction is
            # enrichment: if it fails, the decisions, actions and transcript
            # this source already produced are all still persisted.
            await _process_entities(
                client, tid, s, source_artifact_ids.get(s["src"]), summary)
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
