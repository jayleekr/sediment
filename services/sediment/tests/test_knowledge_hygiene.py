"""sediment#143 — measure whether accumulated knowledge is still worth reading.

reliability_daily already watches whether the PIPELINE runs: is the vault
fresh, did distill extract anything, is recall holding. None of that asks
whether what accumulated is any good.

Once several people and several sources keep adding, knowledge rots on its own —
contradictions nobody adjudicates, synthesized pages nothing points at, questions
the corpus keeps failing to answer. Nothing measured any of it, so it could rot
unnoticed indefinitely.

These test the summariser's judgment directly; the collector is a set of
independent COUNT queries that a live DB exercises.
"""
from __future__ import annotations

import pytest

from validator.checks.reliability_daily import (
    DEFAULT_SLOS,
    summarize_knowledge_hygiene,
)


def _metrics(**over):
    base = {
        "available": True,
        "unavailable_metrics": {},
        "open_contradictions": 0,
        "orphan_derived": 0,
        "stale_derived": 0,
        "derived_from_links": 5,
        "answers_total": 100,
        "answers_ungrounded": 5,
        "stale_context_packs": 0,
    }
    base.update(over)
    return base


def _codes(warnings):
    return {w["code"] for w in warnings}


# ---------------------------------------------------------------------------
# Healthy baseline
# ---------------------------------------------------------------------------

def test_healthy_corpus_produces_no_warnings():
    section, warnings = summarize_knowledge_hygiene(_metrics(), DEFAULT_SLOS)
    assert warnings == []
    assert section["status"] == "ok"
    assert section["ungrounded_ratio"] == 0.05


def test_db_unavailable_degrades_honestly():
    section, warnings = summarize_knowledge_hygiene(
        {"available": False, "error": "connection refused"}, DEFAULT_SLOS)
    assert section["status"] == "unavailable"
    assert "db_unavailable_hygiene" in _codes(warnings)


# ---------------------------------------------------------------------------
# Each rot signal
# ---------------------------------------------------------------------------

def test_piling_up_contradictions_is_a_major_warning():
    """#141 made contradictions representable. Leaving them unrepresented was
    the old bug; leaving them unwatched would be the new one."""
    section, warnings = summarize_knowledge_hygiene(
        _metrics(open_contradictions=DEFAULT_SLOS["open_contradictions_max"] + 1),
        DEFAULT_SLOS)
    assert "open_contradictions_accumulating" in _codes(warnings)
    assert next(w for w in warnings
                if w["code"] == "open_contradictions_accumulating")["severity"] == "major"


def test_contradictions_at_the_threshold_do_not_warn():
    """Some open conflicts are normal — a knowledge layer that never disagrees
    with itself is one nobody is using."""
    _s, warnings = summarize_knowledge_hygiene(
        _metrics(open_contradictions=DEFAULT_SLOS["open_contradictions_max"]),
        DEFAULT_SLOS)
    assert warnings == []


def test_orphan_derived_pages_warn():
    _s, warnings = summarize_knowledge_hygiene(
        _metrics(orphan_derived=DEFAULT_SLOS["orphan_derived_max"] + 1), DEFAULT_SLOS)
    assert "orphan_derived_pages" in _codes(warnings)


def test_any_stale_derived_page_warns():
    """Unlike orphans there is no acceptable baseline: a synthesized page whose
    evidence changed is wrong, not merely untidy."""
    _s, warnings = summarize_knowledge_hygiene(
        _metrics(stale_derived=1), DEFAULT_SLOS)
    assert "stale_derived_pages" in _codes(warnings)


def test_coverage_gap_warns_and_names_the_likely_cause():
    section, warnings = summarize_knowledge_hygiene(
        _metrics(answers_total=100, answers_ungrounded=40), DEFAULT_SLOS)
    assert "coverage_gap" in _codes(warnings)
    assert section["ungrounded_ratio"] == 0.4
    msg = next(w for w in warnings if w["code"] == "coverage_gap")["message"]
    assert "missing sources" in msg


def test_stale_context_packs_warn():
    _s, warnings = summarize_knowledge_hygiene(
        _metrics(stale_context_packs=2), DEFAULT_SLOS)
    assert "stale_context_packs" in _codes(warnings)


# ---------------------------------------------------------------------------
# Honesty about what cannot be measured
# ---------------------------------------------------------------------------

def test_zero_with_nothing_to_measure_is_called_out():
    """A zero meaning "nothing to measure" reads exactly like a zero meaning
    "all healthy". That is how a metric quietly stops being one."""
    section, _w = summarize_knowledge_hygiene(
        _metrics(derived_from_links=0, stale_derived=0), DEFAULT_SLOS)
    assert any("not yet meaningful" in n for n in section["notes"])


def test_a_metric_that_could_not_be_collected_is_reported_not_hidden():
    """A cluster missing migration 008/009 must still get the metrics it CAN
    produce — and be told which ones it did not."""
    section, warnings = summarize_knowledge_hygiene(
        _metrics(open_contradictions=None,
                 unavailable_metrics={"open_contradictions": "no such table"}),
        DEFAULT_SLOS)
    assert "hygiene_metric_unavailable_open_contradictions" in _codes(warnings)
    assert section["open_contradictions"] is None
    # The other metrics still made it through.
    assert section["answers_total"] == 100


def test_no_answers_yet_does_not_divide_by_zero():
    section, warnings = summarize_knowledge_hygiene(
        _metrics(answers_total=0, answers_ungrounded=0), DEFAULT_SLOS)
    assert section["ungrounded_ratio"] is None
    assert "coverage_gap" not in _codes(warnings)


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

def test_hygiene_is_part_of_the_daily_report():
    from validator.checks import reliability_daily as rd
    src = __import__("pathlib").Path(rd.__file__).read_text()
    assert '"knowledge_hygiene": hygiene' in src
    assert "collect_knowledge_hygiene(tenant_slug, since_hours=168)" in src, (
        "rot is measured over weeks, not since yesterday"
    )


@pytest.mark.parametrize("slo", [
    "open_contradictions_max", "orphan_derived_max",
    "context_pack_max_age_hours", "ungrounded_answer_ratio_max",
])
def test_thresholds_are_configurable_slos(slo):
    assert slo in DEFAULT_SLOS
