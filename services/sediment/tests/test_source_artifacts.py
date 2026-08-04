"""sediment#161 — captured transcripts must become citable artifacts.

Capture is wide by design (v0.3 §4: all channels, no allow-list), but retrieval
reads only chunks⨝artifacts and the only thing distill ever wrote back was
decisions. Everything the LLM did not classify as a decision was captured and
then unreachable — not searchable, not citable, and not linkable as evidence
for the decisions drawn from it.

Offline: pure shaping logic plus fakes for the ingest/link calls. No DB, no
LLM, no network.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from scripts.distill import (
    _ingest_source_artifacts,
    _source_artifact_markdown,
)

REPO = Path(__file__).resolve().parents[3]
SVC = REPO / "services" / "sediment"


def _discord_source(channel="weekly", day="2026-05-19", lines=("[jay] 배포 나갔어요",)):
    return {
        "src": f"discord/{channel}/{day}",
        "title": f"#{channel} {day}",
        "channel": channel,
        "strategy": "meeting_transcript",
        "messages": [{"role": "user", "content": "\n".join(lines)}],
        "provenance": {
            "kind": "discord_events",
            "source": "discord",
            "channel": channel,
            "source_date": day,
            "source_event_ids": ["e1", "e2"],
            "source_message_ids": ["m1"],
            "source_event_count": 2,
            "source_started_at": "2026-05-19T01:00:00+00:00",
            "source_ended_at": "2026-05-19T09:00:00+00:00",
        },
    }


def _conversation_source():
    return {
        "src": "conv/abc",
        "title": "some chat",
        "strategy": "chat_thread",
        "messages": [{"role": "user", "content": "hello"}],
        "provenance": {"kind": "conversation", "conversation_id": "abc"},
    }


# ---------------------------------------------------------------------------
# Artifact shape
# ---------------------------------------------------------------------------

def test_ref_is_the_group_identity_so_reruns_update_in_place():
    """`src` is already unique per (channel, day), so a second run over the
    same day updates the transcript rather than duplicating it."""
    ref, _body, _content = _source_artifact_markdown(_discord_source())
    assert ref == "discord/weekly/2026-05-19"
    ref2, _, _ = _source_artifact_markdown(_discord_source(lines=("[jay] 추가 발언",)))
    assert ref2 == ref


def test_frontmatter_marks_it_as_captured_source_not_synthesis():
    _ref, body, _content = _source_artifact_markdown(_discord_source())
    fm = yaml.safe_load(body.split("---")[1])
    assert fm["type"] == "message"
    assert fm["source"] == "discord"
    assert fm["channel"] == "weekly"
    # `date` feeds artifacts.date, which the freshness intent orders by.
    assert fm["date"] == "2026-05-19"


def test_provenance_survives_into_the_artifact():
    """The event ids are the only way back from a chunk to the raw capture."""
    _ref, body, _content = _source_artifact_markdown(_discord_source())
    fm = yaml.safe_load(body.split("---")[1])
    assert fm["provenance"]["source_event_ids"] == ["e1", "e2"]
    assert fm["provenance"]["source_event_count"] == 2


def test_transcript_text_is_in_the_body():
    _ref, body, content = _source_artifact_markdown(
        _discord_source(lines=("[jay] 배포 나갔어요", "[ryan] 확인했습니다")))
    assert "배포 나갔어요" in body
    assert "확인했습니다" in body
    # `content` is what the ingester stores after stripping frontmatter — the
    # unchanged-check compares against artifacts.body, so it must not include it.
    assert not content.lstrip().startswith("---")
    assert "배포 나갔어요" in content


# ---------------------------------------------------------------------------
# Which sources are landed
# ---------------------------------------------------------------------------

class _FakeClient:
    def __init__(self, status=200):
        self.status = status
        self.posts: list[dict] = []

    async def post(self, _url, timeout=None, json=None):
        self.posts.append(json)

        class _R:
            status_code = self.status

            @staticmethod
            def json():
                return {"artifact_id": f"aid-{len(self.posts)}"}

        return _R()


async def _run(sources, existing=None, client=None):
    """Drive _ingest_source_artifacts with the DB lookup stubbed out.

    Patched with a real coroutine function rather than a side_effect: mock
    wraps async targets in an AsyncMock, so a side_effect that itself returns
    a coroutine gets double-wrapped and awaits to a coroutine object.
    """
    summary = {"flags": []}
    client = client or _FakeClient()
    store = existing or {}

    async def _fake_lookup(_tid, ref):
        return store.get(ref)

    with patch("scripts.distill._existing_artifact_body", _fake_lookup):
        mapping = await _ingest_source_artifacts(client, "t", sources, summary)
    return mapping, summary, client


async def test_conversations_are_not_published_to_the_shared_vault():
    """`messages` already stores them, they are per-member rather than shared,
    and publishing chat logs is a visibility decision (#140) that belongs with
    whoever sets policy — not with a batch job."""
    mapping, _summary, client = await _run([_conversation_source()])
    assert mapping == {}
    assert client.posts == []


async def test_discord_capture_is_landed_as_raw_message():
    mapping, summary, client = await _run([_discord_source()])
    assert mapping == {"discord/weekly/2026-05-19": "aid-1"}
    assert summary["source_artifacts"] == 1
    posted = client.posts[0]
    assert posted["type"] == "message"
    # origin='raw': we stored this, we did not write it. #140's layering and
    # #143's hygiene metrics both read 'derived' as "Sediment wrote this".
    assert posted["origin"] == "raw"
    assert posted["ref"] == "discord/weekly/2026-05-19"


async def test_unchanged_day_is_not_re_embedded():
    """A live day is re-derived on every run and each ingest deletes and
    re-embeds every chunk. Cost must track new conversation, not scheduler
    frequency."""
    src = _discord_source()
    _ref, _body, content = _source_artifact_markdown(src)
    mapping, summary, client = await _run(
        [src], existing={"discord/weekly/2026-05-19": ("aid-existing", content)})
    assert client.posts == [], "identical body must not be re-ingested"
    assert mapping == {"discord/weekly/2026-05-19": "aid-existing"}
    assert summary["source_artifacts_unchanged"] == 1


async def test_changed_day_is_re_ingested():
    src = _discord_source()
    mapping, summary, client = await _run(
        [src], existing={"discord/weekly/2026-05-19": ("aid-existing", "older text")})
    assert len(client.posts) == 1
    assert mapping["discord/weekly/2026-05-19"] == "aid-1"


async def test_failed_ingest_is_flagged_loudly():
    """A dropped capture is silently unsearchable, which is the failure mode
    this whole issue is about."""
    mapping, summary, _client = await _run([_discord_source()],
                                           client=_FakeClient(status=500))
    assert mapping == {}
    assert any("not citable" in f for f in summary["flags"])


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

def _read(rel: str) -> str:
    return (SVC / rel).read_text()


def test_transcripts_land_before_the_llm_gate():
    """Making a conversation searchable is storage, not synthesis. Landing it
    after the ANTHROPIC_API_KEY check would mean a tenant without a key
    captures everything and retrieves none of it."""
    src = _read("scripts/distill.py")
    assert src.index("_ingest_source_artifacts(") < src.index("have_llm = bool(")


def test_decision_pages_link_back_to_the_capture():
    """The first derived_from edge in the system that points at real captured
    source text — #143's stale_derived has been reporting "not yet meaningful"
    precisely because none existed."""
    src = _read("scripts/distill.py")
    assert "_link_decision_to_source(" in src
    assert '"derived_from"' in src


@pytest.mark.parametrize("counter", [
    "source_artifacts", "source_artifacts_unchanged", "evidence_links",
])
def test_run_summary_reports_the_new_work(counter):
    src = _read("scripts/distill.py")
    assert f'"{counter}"' in src
