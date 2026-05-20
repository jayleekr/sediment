"""Prompt strategy loader for Distill + Governance agents.

Loads YAML strategy files from `services/sediment/prompts/{agent}/...`,
merges `base.yaml` with the named strategy, and applies tenant addendum
(append-only — never replaces the base safety contract).

Design contract (mirrors `prompts/README.md`):

  | What                       | Override allowed? | Floor / ceiling          |
  |----------------------------|-------------------|--------------------------|
  | system_prompt (base)       | ❌ no override     | —                        |
  | system_prompt addendum     | ✅ append-only     | —                        |
  | tool_schema                | ❌ frozen          | —                        |
  | confidence_threshold       | ✅ within bounds   | floor in base.yaml       |
  | min_body_chars             | ✅ within bounds   | floor in base.yaml       |
  | guards                     | ❌ append-only     | —                        |

Usage:
  from lab_lib.prompts import load_strategy, render_messages
  strat = load_strategy("distill", "chat_thread")
  msgs  = render_messages(strat, user_text="...")
  resp  = client.messages.create(
      model=..., system=strat.system_prompt, tools=[strat.tool_schema],
      tool_choice={"type":"tool","name":strat.tool_schema["name"]},
      messages=msgs,
  )

The loader is intentionally read-only + side-effect-free + cached. Tenant
addendum fetch is stubbed (no-op) until the `tenant_prompt_overrides` table
ships — when it does, only `get_tenant_addendum` needs swapping.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# Repo-relative root: services/sediment/prompts/
_PROMPTS_ROOT = Path(__file__).resolve().parent.parent / "prompts"

# Hard floors — these mirror the README contract. Even if a tenant config
# row tries to undercut them, we clamp here.
_THRESHOLD_HARD_FLOOR = {
    "distill": 0.5,
    "governance": 0.55,
}
_MIN_BODY_HARD_FLOOR = 30


class PromptLoadError(RuntimeError):
    """Raised when a strategy file is missing / malformed."""


@dataclass
class Strategy:
    """Fully-resolved prompt strategy ready to ship to the model."""
    agent: str
    name: str
    version: str
    system_prompt: str          # base + guards + tenant addendum
    tool_schema: dict[str, Any] # frozen — base only
    confidence_threshold: float
    min_body_chars: int
    guards: list[str]
    few_shot: list[dict[str, Any]] = field(default_factory=list)
    applies_to: dict[str, Any] = field(default_factory=dict)

    @property
    def prompt_version(self) -> str:
        """Stable identifier to stamp on extraction metadata for audit."""
        return f"{self.agent}/{self.name}@{self.version}"


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------

@lru_cache(maxsize=64)
def _read_yaml(path_str: str) -> dict[str, Any]:
    p = Path(path_str)
    if not p.exists():
        raise PromptLoadError(f"prompt file missing: {p}")
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise PromptLoadError(f"prompt file root must be a mapping: {p}")
    return data


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge `override` over `base`. dicts merge recursively; lists in
    override REPLACE base lists (strategies declare their own few-shot).
    For `guards`, callers should append explicitly — we do not merge guards
    here because we want the base safety guards to always lead the list."""
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


# ---------------------------------------------------------------------------
# Tenant addendum hook — stub for now
# ---------------------------------------------------------------------------

def get_tenant_addendum(
    tenant_id: str | None, agent: str, name: str
) -> dict[str, Any]:
    """Return tenant-specific overrides for this (agent, strategy).

    Stub: returns empty until the `tenant_prompt_overrides` table exists.
    Once shipped, this should read:
      SELECT prompt_addendum, confidence_threshold, min_body_chars
      FROM tenant_prompt_overrides
      WHERE tenant_id = $1 AND agent = $2 AND strategy = $3

    Returns a dict with optional keys:
      prompt_addendum:        str — appended to system_prompt
      confidence_threshold:   float — clamped to [floor, 0.95]
      min_body_chars:         int  — clamped to [_MIN_BODY_HARD_FLOOR, 250]
      extra_guards:           list[str] — appended to guards
    """
    if not tenant_id:
        return {}
    return {}  # no-op until DB schema lands


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def load_strategy(
    agent: str,
    name: str,
    tenant_id: str | None = None,
) -> Strategy:
    """Load + merge + apply tenant addendum. Returns a Strategy ready to use.

    Args:
      agent:     "distill" | "governance"
      name:      strategy filename without .yaml (e.g. "chat_thread")
      tenant_id: UUID string; if set, tenant addendum is applied
    """
    if agent not in ("distill", "governance"):
        raise PromptLoadError(f"unknown agent: {agent!r}")

    base_path = _PROMPTS_ROOT / agent / "base.yaml"
    strat_path = _PROMPTS_ROOT / agent / "strategies" / f"{name}.yaml"

    base = _read_yaml(str(base_path))
    # The base case ("use base directly, no strategy"): allow name == "base"
    # to load just the base file. Useful for governance scans where the
    # generic base prompt is sufficient.
    strat: dict[str, Any] = {}
    if name == "base":
        merged = copy.deepcopy(base)
    else:
        if not strat_path.exists():
            raise PromptLoadError(
                f"strategy not found: {agent}/{name}. Looked at {strat_path}"
            )
        strat = _read_yaml(str(strat_path))
        merged = _deep_merge(base, strat)

    # Tool schema is ALWAYS base — strategies cannot redefine it.
    merged["tool_schema"] = base.get("tool_schema") or {}
    if not merged["tool_schema"]:
        raise PromptLoadError(f"base.yaml for {agent} missing tool_schema")

    # Compose system prompt: BASE prompt + (optional) strategy addendum.
    # Strategy YAML files conventionally start with "(BASE PROMPT applies. …)"
    # as a self-documenting preamble — they are ADDITIONS to base, not
    # replacements. The deep-merge above replaced base.system_prompt with
    # strat.system_prompt; we now restore the proper composition.
    base_system = str(base.get("system_prompt") or "").rstrip()
    strat_system = str(strat.get("system_prompt") or "").rstrip() if name != "base" else ""
    system_prompt = base_system
    if strat_system:
        system_prompt += "\n\n--- strategy-specific guidance ---\n" + strat_system

    # Tenant addendum (stub for now)
    addendum = get_tenant_addendum(tenant_id, agent, name) if tenant_id else {}
    if addendum.get("prompt_addendum"):
        system_prompt += "\n\n--- tenant addendum ---\n" + addendum["prompt_addendum"].strip()

    # Compose guards into system prompt (base guards first, then strategy
    # guards, then tenant extra guards — safety guards always lead).
    base_guards = list(base.get("guards") or [])
    strat_guards = []
    if name != "base":
        for g in (strat.get("guards") or []):
            if g not in base_guards:
                strat_guards.append(g)
    extra_guards = list(addendum.get("extra_guards") or [])

    all_guards = base_guards + strat_guards + extra_guards
    if all_guards:
        system_prompt += "\n\n--- guards (never relax) ---\n"
        for g in all_guards:
            system_prompt += f"- {g}\n"

    # Threshold + body floor — clamp tenant overrides against hard floors.
    threshold = float(merged.get("confidence_threshold") or 0.6)
    if addendum.get("confidence_threshold") is not None:
        threshold = float(addendum["confidence_threshold"])
    floor = _THRESHOLD_HARD_FLOOR.get(agent, 0.5)
    threshold = max(floor, min(0.95, threshold))

    min_body = int(merged.get("min_body_chars") or 50)
    if addendum.get("min_body_chars") is not None:
        min_body = int(addendum["min_body_chars"])
    min_body = max(_MIN_BODY_HARD_FLOOR, min(250, min_body))

    return Strategy(
        agent=agent,
        name=name,
        version=str(merged.get("version") or "0.0.0"),
        system_prompt=system_prompt.strip(),
        tool_schema=merged["tool_schema"],
        confidence_threshold=threshold,
        min_body_chars=min_body,
        guards=all_guards,
        few_shot=list(merged.get("few_shot") or []),
        applies_to=dict(merged.get("applies_to") or {}),
    )


# ---------------------------------------------------------------------------
# Few-shot rendering
# ---------------------------------------------------------------------------

def render_messages(
    strategy: Strategy,
    user_text: str,
    include_few_shot: bool = True,
) -> list[dict[str, Any]]:
    """Build the Anthropic `messages=[...]` payload.

    Few-shot examples are rendered as alternating user / assistant turns,
    where the assistant turn carries a tool_use block matching the
    strategy's tool_schema. This teaches the model the exact tool-call
    shape without us prefilling it.

    For governance strategies that have no `few_shot`, this is just the
    single user turn.
    """
    msgs: list[dict[str, Any]] = []
    tool_name = strategy.tool_schema.get("name") or "record"

    if include_few_shot and strategy.few_shot:
        for ex in strategy.few_shot:
            if not isinstance(ex, dict):
                continue
            inp = ex.get("input")
            out = ex.get("output")
            if not inp or out is None:
                continue
            msgs.append({"role": "user", "content": str(inp).rstrip()})
            msgs.append({
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": f"few_shot_{len(msgs)}",
                    "name": tool_name,
                    "input": out,
                }],
            })
            # Provide a stub tool_result so the next user turn is valid.
            msgs.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": f"few_shot_{len(msgs) - 1}",
                    "content": "recorded",
                }],
            })

    msgs.append({"role": "user", "content": user_text})
    return msgs


__all__ = [
    "Strategy",
    "PromptLoadError",
    "load_strategy",
    "render_messages",
    "get_tenant_addendum",
]
