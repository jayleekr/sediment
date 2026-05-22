"""Provider-free tests for chat citation hard gates."""
from __future__ import annotations

from lab_lib.grounding import (
    citation_failure_answer,
    extract_inline_refs,
    no_evidence_answer,
    validate_citation_refs,
)


pytestmark = []


def test_extract_inline_refs_supports_multi_refs():
    assert extract_inline_refs("Alpha [1], beta [2, 3], repeat [1].") == (1, 2, 3)


def test_validate_citation_refs_passes_valid_refs():
    result = validate_citation_refs("Answer uses [1] and [2].", citation_count=2)
    assert result.passed is True
    assert result.valid_refs == (1, 2)
    assert result.invalid_refs == ()


def test_validate_citation_refs_fails_missing_refs():
    result = validate_citation_refs("Answer with no brackets.", citation_count=2)
    assert result.passed is False
    assert result.inline_refs == ()


def test_validate_citation_refs_fails_invalid_refs():
    result = validate_citation_refs("Answer cites [3].", citation_count=2)
    assert result.passed is False
    assert result.invalid_refs == (3,)


def test_no_evidence_answer_is_deterministic_by_language():
    assert "vault" in no_evidence_answer("what is latest?")
    assert "근거" in no_evidence_answer("최신 결정 뭐야?")


def test_citation_failure_answer_is_deterministic_by_language():
    assert "citation validation failed" in citation_failure_answer("what happened?")
    assert "인용 검증" in citation_failure_answer("무슨 일이야?")
