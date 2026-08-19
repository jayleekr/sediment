"""sediment#144 — good answers must compound, without poisoning the corpus.

The feedback loop only captured FAILURES: a bad answer became a golden-query
proposal (promote_to_golden.py), a good one sank into the conversation log, and
the next person asking the same thing paid for the same retrieval and the same
synthesis again. "Knowledge compounds" is exactly the half that was missing.

The danger in adding it is the obvious one — answers grounded on answers turns a
knowledge layer into a rumour mill. Two properties prevent that and both are
tested here: the bar for filing is evidence rather than popularity, and a filed
page is never allowed to outrank the sources it came from.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from applications.sediment_platform.routers.promote_to_question import (
    MIN_FAITHFULNESS,
    PROMOTED_ANSWER_CONFIDENCE,
    _page_markdown,
    _slug,
)

REPO = Path(__file__).resolve().parents[3]
SVC = REPO / "services" / "sediment"


def _read(rel: str) -> str:
    return (SVC / rel).read_text()


SRC = None


def setup_module(_m):
    global SRC
    SRC = _read("applications/sediment_platform/routers/promote_to_question.py")


# ---------------------------------------------------------------------------
# The bar is evidence, not popularity
# ---------------------------------------------------------------------------

def test_a_thumbs_up_alone_does_not_qualify():
    """An answer can be liked and wrong. Popularity is not evidence."""
    assert 'detail="answer has no thumbs_up' in SRC
    assert "grounding_status != \"ok\"" in SRC
    assert "faithfulness is not None and faithfulness < MIN_FAITHFULNESS" in SRC


def test_an_ungrounded_answer_is_refused_by_name():
    """This is the single most important rejection: an answer with no evidence
    is precisely what must never become evidence."""
    assert "ungrounded answer is exactly what must not become a source" in SRC


def test_unjudged_answers_are_not_rejected():
    """Most answers are never judged. Requiring a judge score would make the
    endpoint useless; a judged-and-FAILING answer is the different case."""
    assert "faithfulness is not None and" in SRC, (
        "the faithfulness gate must skip unjudged answers, not reject them"
    )


def test_rejections_are_409_not_silent_success():
    """Each admission gate refuses with a 409 naming which condition failed —
    never a quiet success.

    Asserted by cause rather than by counting 409s: sediment#162 added a fourth
    409 for the rev conflict, and a count would have flagged that correct
    change as a regression.
    """
    for cause in ("no thumbs_up", "not 'ok'", "is below"):
        assert cause in SRC, f"no 409 gate mentions {cause!r}"
    assert SRC.count("status_code=409") >= 3


# ---------------------------------------------------------------------------
# A promoted page must not outrank its own evidence
# ---------------------------------------------------------------------------

def test_promoted_confidence_is_low():
    assert 0 < PROMOTED_ANSWER_CONFIDENCE < 0.5, (
        "a page derived from an answer is weaker evidence than what that answer "
        "cited, and must be scored that way"
    )


def test_page_is_marked_derived_and_carries_its_confidence():
    assert '"origin": "derived"' in SRC
    assert '"confidence": PROMOTED_ANSWER_CONFIDENCE' in SRC


@pytest.mark.parametrize("rel", [
    "applications/sediment_platform/routers/library.py",
    "applications/sediment_langgraph/graphs/lab_curator_graph.py",
])
def test_retrieval_demotes_low_confidence_derived_pages(rel):
    """The ranking half of the rule. Without this the page is marked weak and
    still ranks like a source."""
    src = _read(rel)
    assert ("CASE WHEN a.origin = 'derived' AND a.confidence IS NOT NULL\n"
            in src.replace("                       ", "").replace("                           ", "")
            or "a.origin = 'derived' AND a.confidence IS NOT NULL" in src)
    assert "THEN a.confidence ELSE 1.0 END" in src


def test_demotion_is_inert_for_existing_rows():
    """Only promoted pages carry a non-NULL confidence, so this changes no
    existing ranking — the reason it can ship without a recall benchmark."""
    src = _read("applications/sediment_platform/routers/library.py")
    assert "a.confidence IS NOT NULL" in src
    assert "ELSE 1.0 END" in src


def test_evidence_chain_is_recorded():
    """Traceability is what makes answers-grounded-on-answers visible rather
    than inferred."""
    assert '"derived_from"' in SRC
    assert "evidence_chunk_ids=chunk_ids" in SRC


# ---------------------------------------------------------------------------
# Page shape
# ---------------------------------------------------------------------------

def test_page_carries_question_answer_and_evidence():
    md = _page_markdown("Why Postgres", "왜 Postgres 인가?", "Because RLS.",
                        ["spec.md", "decision/db"], note="clear answer")
    assert "type: question" in md
    assert "answer_source: promoted_answer" in md
    assert f"confidence: {PROMOTED_ANSWER_CONFIDENCE}" in md
    assert "**Q.** 왜 Postgres 인가?" in md
    assert "## Evidence" in md
    assert "- `spec.md`" in md


def test_page_without_citations_omits_the_evidence_section():
    md = _page_markdown("T", "Q?", "A", [], None)
    assert "## Evidence" not in md


@pytest.mark.parametrize("title,expected", [
    ("Why Postgres?", "why-postgres"),
    ("왜 Postgres 인가?", "왜-postgres-인가"),
    ("   ", "question"),
])
def test_slugging(title, expected):
    assert _slug(title) == expected


def test_min_faithfulness_is_a_real_floor():
    assert 0.5 <= MIN_FAITHFULNESS <= 0.9


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

def test_router_is_registered_alongside_promote_to_golden():
    src = _read("applications/sediment_platform/main.py")
    assert "promote_to_question" in src
    assert 'app.include_router(promote_to_question.router, prefix="/api/v1/feedback")' in src


def test_promotion_is_audited_as_an_event():
    assert "'question.promoted'" in SRC
