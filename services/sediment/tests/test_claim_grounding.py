"""Provider-free claim-level grounding tests."""
from __future__ import annotations

from lab_lib.grounding import (
    evaluate_claim_grounding,
    is_factual_sentence,
    split_claim_sentences,
)
from validator.checks.p2_grounding import check_claim_grounding_contract


def test_split_claim_sentences_preserves_refs():
    assert split_claim_sentences("Alpha is true [1]. Beta follows [2]?") == (
        "Alpha is true [1].",
        "Beta follows [2]?",
    )


def test_factual_sentence_heuristic_filters_questions_and_preferences():
    assert is_factual_sentence("Sediment validates citations [1].")
    assert is_factual_sentence("세디먼트는 근거 인용을 검증한다 [1].")
    assert not is_factual_sentence("Could you check this?")
    assert not is_factual_sentence("I think this is useful.")


def test_claim_grounding_fully_supported():
    report = evaluate_claim_grounding(
        "Sediment enforces valid inline citations before streaming an answer [1].",
        [{"content": "Sediment enforces valid inline citations before streaming an answer."}],
    )
    assert report.passed is True
    assert report.support_score == 1.0
    assert report.claims[0].verdict == "supported"


def test_claim_grounding_partially_supported():
    report = evaluate_claim_grounding(
        "Sediment validates citations before streaming and publishes a daily report [1].",
        [{"content": "Sediment validates citations before streaming assistant answer deltas."}],
    )
    assert report.support_score == 0.5
    assert report.claims[0].verdict == "partially_supported"


def test_claim_grounding_unsupported_when_missing_or_wrong_refs():
    missing = evaluate_claim_grounding("Sediment validates citations.", [{"content": "Sediment validates citations."}])
    invalid = evaluate_claim_grounding("Sediment validates citations [2].", [{"content": "Sediment validates citations."}])
    wrong = evaluate_claim_grounding("Mars orbits Venus in production [1].", [{"content": "Sediment stores citations."}])
    assert missing.claims[0].verdict == "unsupported"
    assert missing.claims[0].reason == "missing_citation_ref"
    assert invalid.claims[0].reason == "invalid_citation_ref"
    assert wrong.claims[0].reason == "cited_text_does_not_support_claim"


def test_claim_grounding_llm_judge_skips_when_disabled():
    report = evaluate_claim_grounding("Sediment validates citations [1].", [{"content": "Sediment validates citations."}])
    assert report.llm_judge == {"enabled": False, "skipped": True, "reason": "opt-in disabled"}


def test_validator_claim_grounding_contract():
    result = check_claim_grounding_contract({"threshold": 0.75})
    assert result["passed"] is True
    assert result["actual"]["cases"][0]["claims"][0]["verdict"] == "supported"
    assert result["actual"]["cases"][1]["unsupported"] == 1
    assert result["actual"]["cases"][2]["unsupported"] == 1
