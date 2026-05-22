"""Grounding helpers for cited answers.

These functions are deliberately deterministic and provider-free. Runtime
chat, validator checks, and unit tests all use the same citation-index rules
so a model cannot pass one path while failing another.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from os import environ
from typing import Any


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


@dataclass(frozen=True)
class ClaimGrounding:
    text: str
    factual: bool
    refs: tuple[int, ...]
    valid_refs: tuple[int, ...]
    invalid_refs: tuple[int, ...]
    verdict: str
    support_score: float
    reason: str


@dataclass(frozen=True)
class ClaimGroundingReport:
    claims: tuple[ClaimGrounding, ...]
    factual_claims: int
    supported_claims: int
    partially_supported_claims: int
    unsupported_claims: int
    support_score: float
    llm_judge: dict[str, Any]

    @property
    def passed(self) -> bool:
        return self.factual_claims == 0 or self.unsupported_claims == 0


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


_SENTENCE_RE = re.compile(r"[^.!?。！？\n]+(?:[.!?。！？]+|$)", re.MULTILINE)
_TOKEN_RE = re.compile(r"[A-Za-z0-9가-힣][A-Za-z0-9가-힣_-]*")
_STOP_TOKENS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "have", "in", "into", "is", "it", "its", "of", "on", "or", "that",
    "the", "this", "to", "was", "were", "with", "을", "를", "이", "가",
    "은", "는", "의", "에", "에서", "으로", "로", "와", "과",
})
_NON_FACTUAL_PREFIXES = (
    "i think", "maybe", "probably", "could you", "please", "let's", "we should",
    "제 생각", "아마", "해주세요", "합시다",
)


def split_claim_sentences(answer: str) -> tuple[str, ...]:
    """Split answer text into claim-sized sentences, preserving inline refs."""
    sentences: list[str] = []
    for match in _SENTENCE_RE.finditer(answer or ""):
        sentence = " ".join(match.group(0).strip().split())
        if sentence:
            sentences.append(sentence)
    return tuple(sentences)


def is_factual_sentence(sentence: str) -> bool:
    """Heuristic factuality filter for deterministic offline evaluation."""
    s = (sentence or "").strip()
    if not s:
        return False
    lowered = s.lower()
    if lowered.startswith(_NON_FACTUAL_PREFIXES):
        return False
    if s.endswith("?") or s.endswith("？"):
        return False
    body = _BRACKET_RE.sub("", s)
    tokens = _tokens(body)
    if len(tokens) < 2:
        return False
    if re.search(r"\d", body):
        return True
    if any("가" <= ch <= "힣" for ch in body):
        return True
    return any(ch.isupper() for ch in body) or len(tokens) >= 4


def _tokens(text: str) -> set[str]:
    return {
        t.lower()
        for t in _TOKEN_RE.findall(text or "")
        if len(t) >= 2 and t.lower() not in _STOP_TOKENS
    }


def _citation_text(citation: Any) -> str:
    if isinstance(citation, str):
        return citation
    if isinstance(citation, dict):
        parts: list[str] = []
        for key in ("content", "body", "snippet", "text", "summary", "title", "ref"):
            value = citation.get(key)
            if value:
                parts.append(str(value))
        return "\n".join(parts)
    return str(citation or "")


def _support_score(claim: str, cited_text: str) -> float:
    claim_tokens = _tokens(_BRACKET_RE.sub("", claim))
    if not claim_tokens:
        return 0.0
    cited_tokens = _tokens(cited_text)
    if not cited_tokens:
        return 0.0
    denominator = len(claim_tokens)
    return min(1.0, len(claim_tokens & cited_tokens) / denominator)


def _llm_judge_stub(enabled: bool | None) -> dict[str, Any]:
    use_judge = enabled if enabled is not None else environ.get("SEDIMENT_CLAIM_LLM_JUDGE") == "1"
    if not use_judge:
        return {"enabled": False, "skipped": True, "reason": "opt-in disabled"}
    has_provider = any(environ.get(k) for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY"))
    if not has_provider:
        return {"enabled": True, "skipped": True, "reason": "no LLM provider key configured"}
    return {
        "enabled": True,
        "skipped": True,
        "reason": "LLM judge adapter not wired in deterministic validator path",
    }


def evaluate_claim_grounding(
    answer: str,
    citations: list[Any] | tuple[Any, ...],
    *,
    llm_judge_enabled: bool | None = None,
) -> ClaimGroundingReport:
    """Evaluate whether each factual sentence is supported by its cited text.

    This is intentionally conservative. It does not claim semantic proof; it
    catches unsupported or uncited factual claims without any external model.
    """
    citation_count = len(citations)
    claims: list[ClaimGrounding] = []
    for sentence in split_claim_sentences(answer):
        refs_validation = validate_citation_refs(sentence, citation_count)
        factual = is_factual_sentence(sentence)
        if not factual:
            claims.append(ClaimGrounding(
                text=sentence,
                factual=False,
                refs=refs_validation.inline_refs,
                valid_refs=refs_validation.valid_refs,
                invalid_refs=refs_validation.invalid_refs,
                verdict="not_factual",
                support_score=0.0,
                reason="non_factual_sentence",
            ))
            continue
        if refs_validation.invalid_refs:
            verdict, score, reason = "unsupported", 0.0, "invalid_citation_ref"
        elif not refs_validation.valid_refs:
            verdict, score, reason = "unsupported", 0.0, "missing_citation_ref"
        else:
            cited_text = "\n".join(
                _citation_text(citations[n - 1]) for n in refs_validation.valid_refs
            )
            score = _support_score(sentence, cited_text)
            if score >= 0.75:
                verdict, reason = "supported", "token_overlap_supported"
            elif score >= 0.35:
                verdict, reason = "partially_supported", "token_overlap_partial"
            else:
                verdict, reason = "unsupported", "cited_text_does_not_support_claim"
        claims.append(ClaimGrounding(
            text=sentence,
            factual=True,
            refs=refs_validation.inline_refs,
            valid_refs=refs_validation.valid_refs,
            invalid_refs=refs_validation.invalid_refs,
            verdict=verdict,
            support_score=round(score, 3),
            reason=reason,
        ))

    factual_claims = [c for c in claims if c.factual]
    supported = [c for c in factual_claims if c.verdict == "supported"]
    partial = [c for c in factual_claims if c.verdict == "partially_supported"]
    unsupported = [c for c in factual_claims if c.verdict == "unsupported"]
    support_score = (
        (len(supported) + (0.5 * len(partial))) / len(factual_claims)
        if factual_claims else 1.0
    )
    return ClaimGroundingReport(
        claims=tuple(claims),
        factual_claims=len(factual_claims),
        supported_claims=len(supported),
        partially_supported_claims=len(partial),
        unsupported_claims=len(unsupported),
        support_score=round(support_score, 3),
        llm_judge=_llm_judge_stub(llm_judge_enabled),
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
