"""Collection AI Agent — the `decide(event)` function.

For every NormalizedEvent that a connector emits, this module decides:
  - Should we INGEST it (chunk + embed + store as artifact)?
  - Should we NOTIFY (render a template + dispatch via routes)?
  - Which channels?

The decision is driven by:
  1. `source_kind` (set per-integration in `integrations.config.source_kind`).
     One of: vault | product | harness | transcript | artifacts | mixed.
  2. Per-source-kind default rules (declared in this file, mirrored in
     `docs/design/12-source-kinds-catalog.md` — keep them in sync).
  3. Per-integration overrides (`integrations.config.event_rules`) — append
     to defaults; last-match-wins for conflicting `action: []` skips.

The function is PURE — no IO, no DB, no LLM. It takes an event + a config
dict and returns a `CollectionDecision`. The caller (a fetch script or the
collection orchestrator) handles the side effects.

Refactor target: `scripts/github_repo_fetch.py` and (future)
`scripts/discord_fetch.py` should both go through `decide()` instead of
hardcoding their own ingest+notify logic. See `docs/design/04-collection-
engine.md §5` for the design contract this implements.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .connectors.base import NormalizedEvent


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

@dataclass
class CollectionDecision:
    """What to do with one NormalizedEvent. Empty = skip everything."""
    ingest: bool = False
    notify: bool = False
    notify_event_type: str | None = None       # e.g. "new_decision"
    notify_channels: list[str] = field(default_factory=list)
    notify_template: str | None = None         # override default template
    distill_strategy: str | None = None        # for transcript events
    # Reason for the decision — for logs / debugging. Not consumed by callers.
    matched_rule: str = "default"


# ---------------------------------------------------------------------------
# Rule matchers
# ---------------------------------------------------------------------------

def _glob_to_regex(pattern: str) -> str:
    """Translate a `**`-aware glob to a regex matching full paths.

    `**` (with or without trailing `/`) matches zero or more path segments.
    `*` matches any chars except `/`. `?` matches a single non-`/` char.
    Trailing `/` on a pattern matches any path starting with the prefix.
    """
    parts: list[str] = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                # ** matches any chars including /
                parts.append(".*")
                i += 2
                # Consume the optional trailing / so `**/decisions/` matches
                # both `decisions/x.md` and `wiki/decisions/x.md`
                if i < len(pattern) and pattern[i] == "/":
                    i += 1
            else:
                parts.append("[^/]*")
                i += 1
        elif c == "?":
            parts.append("[^/]")
            i += 1
        elif c in r"\.+()[]^$|{}":
            parts.append(re.escape(c))
            i += 1
        else:
            parts.append(re.escape(c) if c in "/" else c)
            i += 1
    suffix = "" if pattern.endswith("/") else "$"
    return "^" + "".join(parts) + suffix


_GLOB_CACHE: dict[str, re.Pattern] = {}


def _path_matches(path: str, patterns: list[str]) -> bool:
    """`**`-aware glob match against the full path string.
    Empty patterns list = always match (rule with no path filter)."""
    if not patterns:
        return True
    for p in patterns:
        if p not in _GLOB_CACHE:
            _GLOB_CACHE[p] = re.compile(_glob_to_regex(p))
        if _GLOB_CACHE[p].match(path):
            return True
    return False


def _path_excluded(path: str, excludes: list[str]) -> bool:
    """Substring match — `.raw/` matches anywhere in the path."""
    return any(seg in path for seg in excludes)


# ---------------------------------------------------------------------------
# Default rules per source_kind
# ---------------------------------------------------------------------------
#
# Each entry is a list of rules, evaluated in order. The first rule whose
# condition matches wins. The `default` rule at the end catches everything.
#
# Rule shape:
#   {
#     "name": str,                       # for debugging
#     "when": dict,                      # match conditions (any subset)
#       "kinds": [str]                   #   event.kind in this list
#       "path_globs": [str]              #   any path matches
#       "path_excludes": [str]           #   path contains none
#       "channel_names": [str]           #   event.payload.channel in this list
#     "action": {                        # what to do
#       "ingest": bool
#       "notify": bool
#       "notify_event_type": str        #   when notify
#       "notify_template": str           #   optional
#       "distill_strategy": str          #   optional, for transcript
#     }
#   }

_DEFAULT_RULES: dict[str, list[dict]] = {
    "vault": [
        {
            "name": "vault.raw_or_obsidian_excluded",
            "when": {"path_excludes": [".raw/", ".obsidian/", "node_modules/"]},
            "action": {"ingest": False, "notify": False},
        },
        {
            "name": "vault.decisions_notify",
            "when": {"path_globs": ["**/decisions/**", "**/adr-*.md"]},
            "action": {
                "ingest": True,
                "notify": True,
                "notify_event_type": "new_decision",
                "notify_template": "new_decision",
            },
        },
        {
            "name": "vault.file_delete",
            "when": {"kinds": ["file_delete"]},
            "action": {"ingest": False, "notify": False},   # caller handles delete separately
        },
        {
            "name": "vault.default",
            "when": {},
            "action": {"ingest": True, "notify": False},
        },
    ],

    "product": [
        {
            "name": "product.release",
            "when": {"kinds": ["release"]},
            "action": {
                "ingest": False,
                "notify": True,
                "notify_event_type": "release_published",
                "notify_template": "release_published",
            },
        },
        {
            "name": "product.deploy_failure",
            "when": {"kinds": ["deploy.failure"]},
            "action": {
                "ingest": False,
                "notify": True,
                "notify_event_type": "deploy.failure",
                "notify_template": "deploy_failure",
            },
        },
        {
            "name": "product.deploy_success",
            "when": {"kinds": ["deploy.success"]},
            "action": {
                "ingest": False,
                "notify": True,
                "notify_event_type": "deploy.success",
                "notify_template": "deploy_success",
            },
        },
        {
            "name": "product.docs_decisions_notify",
            "when": {"path_globs": ["docs/**/decisions/**"]},
            "action": {
                "ingest": True,
                "notify": True,
                "notify_event_type": "new_decision",
                "notify_template": "new_decision",
            },
        },
        {
            "name": "product.docs_md",
            "when": {"path_globs": ["docs/**/*.md"]},
            "action": {"ingest": True, "notify": False},
        },
        {
            "name": "product.root_canonical_md",
            "when": {"path_globs": [
                "SPEC.md", "DECISIONS.md", "README.md",
                "CLAUDE.md", "CHANGELOG.md", "CONTRIBUTING.md",
                "ARCHITECTURE.md",
            ]},
            "action": {"ingest": True, "notify": False},
        },
        {
            "name": "product.default",
            "when": {},
            "action": {"ingest": False, "notify": False},
        },
    ],

    "harness": [
        {
            "name": "harness.skill_published",
            "when": {"path_globs": ["skills/*/SKILL.md"]},
            "action": {
                "ingest": True,
                "notify": True,
                "notify_event_type": "skill_published",
                "notify_template": "skill_published",
            },
        },
        {
            "name": "harness.docs",
            "when": {"path_globs": ["docs/**/*.md", "README.md"]},
            "action": {"ingest": True, "notify": False},
        },
        {
            "name": "harness.default",
            "when": {},
            "action": {"ingest": False, "notify": False},
        },
    ],

    "transcript": [
        {
            "name": "transcript.noise_channels",
            "when": {"channel_names": [
                "공지사항", "rule", "온보딩-가이드", "hackathon", "잡담",
            ]},
            "action": {"ingest": False, "notify": False},
        },
        {
            "name": "transcript.meeting_notes",
            "when": {"channel_names": ["meeting-notes"]},
            "action": {
                "ingest": True, "notify": False,
                "distill_strategy": "meeting_transcript",
            },
        },
        {
            "name": "transcript.default",
            "when": {},
            "action": {
                "ingest": True, "notify": False,
                "distill_strategy": "chat_thread",
            },
        },
    ],

    "artifacts": [
        {
            "name": "artifacts.release",
            "when": {"kinds": ["release"]},
            "action": {
                "ingest": False, "notify": True,
                "notify_event_type": "release_published",
                "notify_template": "release_published",
            },
        },
        {
            "name": "artifacts.default",
            "when": {},
            "action": {"ingest": False, "notify": False},
        },
    ],

    # `mixed` falls back to vault rules + the integration's own event_rules
    # is expected to express the code-vs-content split per-path.
    "mixed": [],
}


# ---------------------------------------------------------------------------
# Rule evaluator
# ---------------------------------------------------------------------------

def _rule_matches(event: NormalizedEvent, when: dict) -> bool:
    """Return True if the event satisfies every clause in `when`."""
    path = (event.payload or {}).get("path") or ""
    channel = (event.payload or {}).get("channel") or ""

    kinds = when.get("kinds")
    if kinds and event.kind not in kinds:
        return False

    globs = when.get("path_globs")
    if globs and not _path_matches(path, globs):
        return False

    excludes = when.get("path_excludes")
    if excludes and not _path_excluded(path, excludes):
        return False

    chans = when.get("channel_names")
    if chans and channel not in chans:
        return False

    return True


def _apply_action(decision: CollectionDecision, action: dict, rule_name: str) -> None:
    """Merge an action dict into a CollectionDecision. Last applied wins
    per-field, but notify_channels accumulate."""
    if "ingest" in action:
        decision.ingest = bool(action["ingest"])
    if "notify" in action:
        decision.notify = bool(action["notify"])
    if "notify_event_type" in action:
        decision.notify_event_type = action["notify_event_type"]
    if "notify_template" in action:
        decision.notify_template = action["notify_template"]
    if "notify_channels" in action:
        # Channels accumulate so override rules can add, not replace
        for ch in action["notify_channels"]:
            if ch not in decision.notify_channels:
                decision.notify_channels.append(ch)
    if "distill_strategy" in action:
        decision.distill_strategy = action["distill_strategy"]
    decision.matched_rule = rule_name


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def decide(event: NormalizedEvent, integration_config: dict | None = None) -> CollectionDecision:
    """Decide what to do with one event. Pure function, no side effects.

    Args:
        event: NormalizedEvent from a connector.
        integration_config: contents of `integrations.config` JSONB for the
            integration that emitted this event. Reads `source_kind` and
            optional `event_rules`/`notify` keys.

    Returns: CollectionDecision (see dataclass docs).
    """
    cfg = integration_config or {}
    source_kind = cfg.get("source_kind") or "vault"   # default = aggressive ingest

    default_rules = _DEFAULT_RULES.get(source_kind, _DEFAULT_RULES["vault"])
    tenant_rules = cfg.get("event_rules") or []

    decision = CollectionDecision()

    # First match in the default rules wins for the base decision
    for rule in default_rules:
        if _rule_matches(event, rule.get("when") or {}):
            _apply_action(decision, rule.get("action") or {}, rule["name"])
            break

    # Tenant overrides — applied IN ORDER, each can refine the base decision.
    # An override with `action: {ingest: False, notify: False}` skips the event
    # entirely (covers tenant-specific blacklists like "this path is too noisy").
    for i, rule in enumerate(tenant_rules):
        if _rule_matches(event, rule.get("when") or {}):
            _apply_action(decision, rule.get("action") or {},
                          f"tenant_override[{i}]:{rule.get('name','?')}")

    # Resolve default notify_channels from the integration config if the
    # decision says notify but didn't pick channels explicitly.
    if decision.notify and not decision.notify_channels:
        cfg_notify = cfg.get("notify") or {}
        defaults = cfg_notify.get("default_channels") or ["primary"]
        decision.notify_channels = list(defaults)

    return decision


# ---------------------------------------------------------------------------
# Helpers exposed for callers
# ---------------------------------------------------------------------------

def list_default_rules(source_kind: str) -> list[dict]:
    """Read-only access to the default rule set for debugging / introspection.
    Returns a shallow copy."""
    return list(_DEFAULT_RULES.get(source_kind) or [])


def supported_source_kinds() -> list[str]:
    return list(_DEFAULT_RULES.keys())
