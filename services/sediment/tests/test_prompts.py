"""Offline unit tests for the prompt strategy loader.

No DB / LLM / network — these prove the merge/floor/freeze contract that
the Distill + Governance agents depend on. Safe under SKIP_DB=1.
"""
from __future__ import annotations

import pytest

from lab_lib.prompts import (
    PromptLoadError,
    Strategy,
    load_strategy,
    render_messages,
)


# ---------------------------------------------------------------------------
# Smoke: can we load every shipped strategy?
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("agent,name", [
    ("distill", "base"),
    ("distill", "chat_thread"),
    ("distill", "meeting_transcript"),
    ("distill", "doc_edit"),
    ("distill", "code_change"),
    ("governance", "base"),
    ("governance", "archive_stale"),
    ("governance", "redact_pii"),
    ("governance", "anomaly_flag"),
])
def test_every_shipped_strategy_loads(agent, name):
    s = load_strategy(agent, name)
    assert isinstance(s, Strategy)
    assert s.system_prompt
    assert s.tool_schema, "tool_schema must be present from base"
    assert s.tool_schema.get("name"), "tool_schema needs a name"
    assert 0.5 <= s.confidence_threshold <= 0.95
    assert 30 <= s.min_body_chars <= 250
    assert s.prompt_version.startswith(f"{agent}/{name}@")


# ---------------------------------------------------------------------------
# Tool schema is frozen — strategies cannot override
# ---------------------------------------------------------------------------

def test_tool_schema_is_base_never_strategy():
    """No matter which distill strategy you load, the tool_schema name must
    be the base's `record_decisions_and_actions`. Downstream parsers depend
    on this — tenants/strategies cannot rename or restructure."""
    base = load_strategy("distill", "base")
    for name in ["chat_thread", "meeting_transcript", "doc_edit", "code_change"]:
        s = load_strategy("distill", name)
        assert s.tool_schema["name"] == base.tool_schema["name"]
        assert s.tool_schema["input_schema"] == base.tool_schema["input_schema"]


def test_governance_tool_schema_is_propose_only():
    s = load_strategy("governance", "redact_pii")
    assert s.tool_schema["name"] == "propose_governance_actions"
    # Output schema must include the 6 governance categories
    cats = (s.tool_schema["input_schema"]["properties"]["actions"]
            ["items"]["properties"]["category"]["enum"])
    for required in [
        "archive", "redact", "cascade_delete",
        "promote_redistill", "preserve", "anomaly_flag",
    ]:
        assert required in cats


# ---------------------------------------------------------------------------
# Guards are append-only (strategy guards come AFTER base guards)
# ---------------------------------------------------------------------------

def test_base_guards_lead_strategy_guards():
    s = load_strategy("distill", "chat_thread")
    # base.yaml's first guard is "Do not invent decisions or actions..."
    sp = s.system_prompt
    invent_idx = sp.find("Do not invent decisions")
    emoji_idx = sp.find("Emoji-only messages")
    assert invent_idx != -1, "base 'do not invent' guard missing"
    assert emoji_idx != -1, "chat_thread emoji guard missing"
    # Base safety guard must appear before strategy-specific guard.
    assert invent_idx < emoji_idx, "base guards must lead strategy guards"


# ---------------------------------------------------------------------------
# Threshold floor — tenant addendum cannot undercut base floor
# ---------------------------------------------------------------------------

def test_distill_threshold_floor_is_05(monkeypatch):
    import lab_lib.prompts as pmod
    monkeypatch.setattr(pmod, "get_tenant_addendum", lambda *a, **k: {
        "confidence_threshold": 0.1,  # try to undercut
    })
    s = load_strategy("distill", "chat_thread", tenant_id="dummy-tenant")
    assert s.confidence_threshold >= 0.5, (
        "tenant must not be able to push distill threshold below 0.5"
    )


def test_governance_threshold_floor_is_055(monkeypatch):
    import lab_lib.prompts as pmod
    monkeypatch.setattr(pmod, "get_tenant_addendum", lambda *a, **k: {
        "confidence_threshold": 0.1,
    })
    s = load_strategy("governance", "redact_pii", tenant_id="dummy-tenant")
    assert s.confidence_threshold >= 0.55


def test_threshold_ceiling_is_095(monkeypatch):
    import lab_lib.prompts as pmod
    monkeypatch.setattr(pmod, "get_tenant_addendum", lambda *a, **k: {
        "confidence_threshold": 0.99,
    })
    s = load_strategy("distill", "chat_thread", tenant_id="dummy-tenant")
    assert s.confidence_threshold <= 0.95


# ---------------------------------------------------------------------------
# Addendum is append-only — never replaces base prompt
# ---------------------------------------------------------------------------

def test_addendum_appends_to_system_prompt(monkeypatch):
    import lab_lib.prompts as pmod
    monkeypatch.setattr(pmod, "get_tenant_addendum", lambda *a, **k: {
        "prompt_addendum": "ACME tenant: emit decisions in English only.",
    })
    s = load_strategy("distill", "chat_thread", tenant_id="acme")
    # Base must still be there
    assert "memory consolidator" in s.system_prompt
    # Addendum must appear AFTER base
    base_idx = s.system_prompt.find("memory consolidator")
    add_idx = s.system_prompt.find("ACME tenant")
    assert add_idx > base_idx


def test_addendum_extra_guards_appended(monkeypatch):
    import lab_lib.prompts as pmod
    monkeypatch.setattr(pmod, "get_tenant_addendum", lambda *a, **k: {
        "extra_guards": ["ACME-specific: never mention competitor names."],
    })
    s = load_strategy("distill", "chat_thread", tenant_id="acme")
    assert "ACME-specific" in s.system_prompt
    assert "Do not invent decisions" in s.system_prompt  # base still leads


# ---------------------------------------------------------------------------
# Unknown agent / missing strategy → PromptLoadError
# ---------------------------------------------------------------------------

def test_unknown_agent_raises():
    with pytest.raises(PromptLoadError):
        load_strategy("not_an_agent", "anything")


def test_missing_strategy_raises():
    with pytest.raises(PromptLoadError):
        load_strategy("distill", "does_not_exist_strategy")


# ---------------------------------------------------------------------------
# Few-shot rendering
# ---------------------------------------------------------------------------

def test_render_messages_includes_few_shot_by_default():
    s = load_strategy("distill", "chat_thread")
    msgs = render_messages(s, user_text="real input here")
    # chat_thread has 2 few-shot examples → 2 * 3 turns + final user = 7
    assert len(msgs) >= 4
    assert msgs[-1]["role"] == "user"
    assert msgs[-1]["content"] == "real input here"
    # First few-shot turn: user → assistant tool_use → user tool_result
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"
    assert isinstance(msgs[1]["content"], list)
    assert msgs[1]["content"][0]["type"] == "tool_use"
    assert msgs[1]["content"][0]["name"] == s.tool_schema["name"]


def test_render_messages_skips_few_shot_when_disabled():
    s = load_strategy("distill", "chat_thread")
    msgs = render_messages(s, user_text="hello", include_few_shot=False)
    assert len(msgs) == 1
    assert msgs[0] == {"role": "user", "content": "hello"}


def test_governance_strategy_without_few_shot_renders_single_turn():
    """Some governance strategies may have empty/missing few_shot — render
    should not break."""
    # archive_stale + redact_pii + anomaly_flag DO have few-shot;
    # base alone does not.
    s = load_strategy("governance", "base")
    msgs = render_messages(s, user_text="event payload here")
    assert msgs[-1]["role"] == "user"
    assert msgs[-1]["content"] == "event payload here"


# ---------------------------------------------------------------------------
# prompt_version is stable + includes version
# ---------------------------------------------------------------------------

def test_prompt_version_stable_format():
    s = load_strategy("distill", "chat_thread")
    pv = s.prompt_version
    assert pv.startswith("distill/chat_thread@")
    assert pv.count("@") == 1
    # Version segment is non-empty
    assert pv.split("@", 1)[1]


# Override conftest's DB skip — these tests don't need DB.
pytestmark = []
