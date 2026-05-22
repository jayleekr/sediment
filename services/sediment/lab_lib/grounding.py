"""Grounding helpers for cited answers.

These functions are deliberately deterministic and provider-free. Runtime
chat, validator checks, and unit tests all use the same citation-index rules
so a model cannot pass one path while failing another.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


_BRACKET_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


@dataclass(frozen=True)
class CitationValidation:
    citation_count: int
    inline_refs: tuple[int, ...]
    valid_refs: tuple[int, ...]
    invalid_refs: tuple[int, ...]

    @property
    def has_valid_ref(self) -> bool:
        return bool(self.valid_refs)

    @property
    def passed(self) -> bool:
        return self.citation_count > 0 and self.has_valid_ref and not self.invalid_refs


def extract_inline_refs(answer: str) -> tuple[int, ...]:
    """Return unique 1-based citation indexes found in an answer.

    Supports compact multi-refs such as ``[1, 3]``. Ranges are intentionally
    unsupported; they are ambiguous for UI citation cards and should fail as
    missing refs until we define that syntax.
    """
    refs: list[int] = []
    seen: set[int] = set()
    for match in _BRACKET_RE.finditer(answer or ""):
        for raw in match.group(1).split(","):
            try:
                n = int(raw.strip())
            except ValueError:
                continue
            if n not in seen:
                seen.add(n)
                refs.append(n)
    return tuple(refs)


def validate_citation_refs(answer: str, citation_count: int) -> CitationValidation:
    inline_refs = extract_inline_refs(answer)
    valid: list[int] = []
    invalid: list[int] = []
    for n in inline_refs:
        if 1 <= n <= citation_count:
            valid.append(n)
        else:
            invalid.append(n)
    return CitationValidation(
        citation_count=citation_count,
        inline_refs=inline_refs,
        valid_refs=tuple(valid),
        invalid_refs=tuple(invalid),
    )


def no_evidence_answer(query: str) -> str:
    """Deterministic response when retrieval returned no evidence."""
    has_ko = any("가" <= ch <= "힣" for ch in query or "")
    if has_ko:
        return (
            "현재 vault에서 이 질문을 뒷받침할 근거를 찾지 못했습니다. "
            "최신 ingest 상태를 확인하거나 더 구체적인 키워드로 다시 물어봐 주세요."
        )
    return (
        "I couldn't find evidence in the vault to support an answer. "
        "Check the latest ingest status or try a more specific query."
    )


def citation_failure_answer(query: str) -> str:
    """Deterministic response after the model failed citation validation."""
    has_ko = any("가" <= ch <= "힣" for ch in query or "")
    if has_ko:
        return (
            "근거 인용 검증에 실패해서 답변을 보류했습니다. "
            "검색된 citation은 있었지만 답변이 유효한 [N] 근거에 묶이지 않았습니다."
        )
    return (
        "I withheld the answer because citation validation failed. "
        "The retrieval returned citations, but the answer did not attach itself to valid [N] references."
    )
