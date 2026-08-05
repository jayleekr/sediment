"""sediment#162 — arm the optimistic lock that #138 built and nobody used.

#138 (PR #149) gave the ingester `expected_rev` and a 409, then defaulted it to
None. Every caller kept the default, so every write stayed last-writer-wins and
a lost update remained invisible. The mechanism existed; nothing fired it.

These pin what each writer does with a conflict, because the right answer
differs: a batch job skips and reports, an interactive caller is told to
re-read, and a convergent upsert is deliberately left unlocked.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.distill import RevConflict, _ingest_artifact

REPO = Path(__file__).resolve().parents[3]
SVC = REPO / "services" / "sediment"


def _read(rel: str) -> str:
    return (SVC / rel).read_text()


class _Resp:
    def __init__(self, status, payload=None, text_body=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text_body

    def json(self):
        return self._payload


class _Client:
    def __init__(self, resp):
        self._resp = resp
        self.posts: list[dict] = []

    async def post(self, _url, timeout=None, json=None):
        self.posts.append(json)
        return self._resp


# ---------------------------------------------------------------------------
# The helper
# ---------------------------------------------------------------------------

async def test_expected_rev_is_sent_to_the_ingester():
    c = _Client(_Resp(200, {"artifact_id": "a1"}))
    aid = await _ingest_artifact(c, "t", "decision/x", "body", "tenant",
                                 expected_rev=5)
    assert aid == "a1"
    assert c.posts[0]["expected_rev"] == 5


async def test_none_expected_rev_keeps_last_writer_wins():
    """The default must not change behaviour for callers that have not opted
    in — the webhook batch is deliberately one of them."""
    c = _Client(_Resp(200, {"artifact_id": "a1"}))
    await _ingest_artifact(c, "t", "decision/x", "body", "tenant")
    assert c.posts[0]["expected_rev"] is None


async def test_409_raises_rather_than_returning_none():
    """A conflict and an outage need opposite responses. Folding both into a
    None return would let a caller treat "somebody else got here first" as
    "the ingester is down"."""
    c = _Client(_Resp(409, text_body="rev conflict on 'decision/x'"))
    with pytest.raises(RevConflict):
        await _ingest_artifact(c, "t", "decision/x", "b", "tenant", expected_rev=1)


async def test_other_failures_still_return_none():
    c = _Client(_Resp(500, text_body="boom"))
    assert await _ingest_artifact(c, "t", "decision/x", "b", "tenant",
                                  expected_rev=1) is None


async def test_transport_errors_do_not_masquerade_as_conflicts():
    class _Boom:
        async def post(self, *_a, **_k):
            raise RuntimeError("connection refused")

    assert await _ingest_artifact(_Boom(), "t", "decision/x", "b", "tenant",
                                  expected_rev=1) is None


# ---------------------------------------------------------------------------
# What the ref resolver hands over
# ---------------------------------------------------------------------------

class _Row:
    def __init__(self, d):
        self._mapping = d


class _Result:
    def __init__(self, rows):
        self._rows = [_Row(r) for r in rows]

    def __iter__(self):
        return iter(self._rows)


class _Session:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, *_a, **_k):
        return _Result(self.rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


async def _resolve(siblings, src="conv/1", body="B"):
    from scripts import distill
    with patch.object(distill, "service_session", lambda: _Session(siblings)):
        return await distill._resolve_decision_ref(
            "t", "decision/use-postgres", src, body)


async def test_reused_page_hands_over_its_rev():
    _ref, _conf, expected_rev = await _resolve([
        {"id": "a1", "ref": "decision/use-postgres", "body": "OLD",
         "source": "conv/1", "rev": 9},
    ], body="NEW")
    assert expected_rev == 9


async def test_new_ref_has_nothing_to_lock_on():
    _ref, _conf, expected_rev = await _resolve([])
    assert expected_rev is None


# ---------------------------------------------------------------------------
# Per-writer policy — the reason this is not one uniform rule
# ---------------------------------------------------------------------------

def test_distill_skips_and_reports_rather_than_retrying():
    """Retrying blind would overwrite whoever won the race — the exact loss
    #138 stopped. The next scheduled run re-reads and re-resolves."""
    src = _read("scripts/distill.py")
    assert "except RevConflict as e:" in src
    assert 'summary["rev_conflicts"]' in src
    assert "skipped, another" in src
    assert "continue" in src


def test_entity_pages_are_deliberately_not_locked():
    """A convergent upsert has no per-source content for a racing writer to
    destroy; locking would only manufacture conflicts between two sources that
    mention the same project."""
    src = _read("scripts/distill.py")
    assert "No expected_rev here, deliberately" in src


def test_promote_to_question_tells_the_caller_to_re_read():
    """Interactive, not batch: a 409 is actionable by the person who triggered
    it, so it surfaces instead of being counted."""
    src = _read("applications/sediment_platform/routers/promote_to_question.py")
    assert "expected_rev" in src
    assert "resp.status_code == 409" in src
    assert "re-read the page and retry" in src


def test_webhook_batch_is_left_last_writer_wins():
    """git push is the source of truth there and competing webhooks converge on
    re-run, so a lock would add failures without preventing a loss."""
    src = _read("applications/vault_ingester/main.py")
    assert "expected_rev: Optional[int] = None" in src


@pytest.mark.parametrize("counter", ["rev_conflicts"])
def test_run_summary_reports_conflicts(counter):
    assert f'"{counter}"' in _read("scripts/distill.py")
