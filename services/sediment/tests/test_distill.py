"""Offline unit tests for the distill "정리" agent pure logic.

No DB / LLM / network — these prove the artifact-shaping + ref-idempotency
that the 5/28 dogfood session depends on. Safe under SKIP_DB=1.
"""
from scripts.distill import _decision_markdown, _slug


def test_slug_idempotent_and_bounded():
    cases = ["Sediment 레포 분리", "Why we did X!! (v2)", "  Trailing  ", "a" * 200]
    for c in cases:
        s = _slug(c)
        assert s == _slug(s), f"slug not idempotent for {c!r}"
        assert len(s) <= 60
        assert s == s.strip("-")
    assert _slug("") == "decision"          # empty → safe default
    assert _slug("!!!") == "decision"       # all-stripped → safe default
    assert "레포" in _slug("Sediment 레포 분리")  # Korean preserved


def test_decision_markdown_ref_is_stable_and_namespaced():
    d = {"topic": "Sediment 레포 분리", "body": "AX SaaS 경로.", "status": "made"}
    ref1, md1 = _decision_markdown(d, "discord/weekly/2026-05-19", "#weekly")
    ref2, _ = _decision_markdown(d, "conv/abc", "other source")
    assert ref1.startswith("decision/")
    # Same topic → same ref regardless of source ⇒ vault upsert = update,
    # not duplicate (the vault-differ new/update/known guarantee).
    assert ref1 == ref2


def test_decision_markdown_frontmatter_and_why_body():
    d = {"topic": "강의용 Studio는 해자가 아님",
         "body": "Cursor류 도구·연료 수집용.", "status": "made"}
    ref, md = _decision_markdown(d, "discord/weekly/2026-05-19",
                                 "#weekly 2026-05-19")
    assert md.startswith("---\n")            # frontmatter block present
    assert 'type: "decision"' in md
    assert 'status: "made"' in md
    assert "강의용 Studio는 해자가 아님" in md   # topic in heading
    assert "Cursor류 도구" in md              # the "왜" / rationale body
    assert "#weekly 2026-05-19" in md        # provenance / source title
    assert "출처:" in md


def test_two_distinct_topics_get_distinct_refs():
    a, _ = _decision_markdown({"topic": "alpha decision", "body": "x"}, "s", "t")
    b, _ = _decision_markdown({"topic": "beta decision", "body": "y"}, "s", "t")
    assert a != b


def test_missing_status_defaults_to_made():
    _, md = _decision_markdown({"topic": "no status", "body": "z"}, "s", "t")
    assert 'status: "made"' in md
