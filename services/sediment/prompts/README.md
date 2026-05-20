# Prompts — Distill & Governance

Externalized LLM prompts + tool schemas for the two agents that keep the
vault healthy. Designed to be **tenant-tunable** (system prompt addendum,
not full override) while preserving Sediment's safety invariants.

## Layout

```
prompts/
├─ README.md                  this file
├─ distill/                   "위로 정제" — signal extraction
│  ├─ base.yaml               default workhorse (supersedes consolidate_memory _SYSTEM/_EXTRACT_TOOL)
│  └─ strategies/             per-source extraction profiles
│     ├─ chat_thread.yaml     Discord/Slack/Telegram threads
│     ├─ meeting_transcript.yaml   Gemini/Otter/Fireflies notes (#meeting-notes)
│     ├─ doc_edit.yaml        Notion/Confluence/Drive page diff
│     └─ code_change.yaml     GitHub/GitLab PR/issue resolution
└─ governance/                "아래로 정제" — value pruning (§14)
   ├─ base.yaml
   └─ strategies/
      ├─ archive_stale.yaml   value assessment of old events
      ├─ redact_pii.yaml      PII classification + redaction proposal
      └─ anomaly_flag.yaml    unusual pattern detection (security/abuse)
```

## How prompts are loaded

```python
# pseudo: services/sediment/lab_lib/prompts.py
def load_strategy(agent: str, name: str, tenant_id: str | None = None) -> Strategy:
    base = yaml.safe_load(open(f"prompts/{agent}/base.yaml"))
    strat = yaml.safe_load(open(f"prompts/{agent}/strategies/{name}.yaml"))
    merged = deep_merge(base, strat)
    if tenant_id:
        addendum = get_tenant_addendum(tenant_id, agent, name)
        if addendum:
            merged["system_prompt"] += "\n\n--- tenant addendum ---\n" + addendum
            # tenant_thresholds can lower BELOW base only down to floor
            apply_thresholds(merged, tenant_id)
    return Strategy(**merged)
```

## Tenant override rules

| What | Override allowed? | Floor / ceiling |
|---|---|---|
| `system_prompt` (base) | ❌ no override | — |
| `system_prompt` addendum | ✅ append-only | — |
| `tool_schema` | ❌ frozen (downstream parsers depend) | — |
| `confidence_threshold` | ✅ lower allowed | floor: 0.5 |
| `min_body_chars` | ✅ lower allowed | floor: 30 |
| `guards` | ❌ append-only (cannot remove safety guards) | — |

**Invariant: base "do not invent" guard is never removable.** Tenant override
cannot make the LLM hallucinate decisions to fill quotas.

## Per-strategy fields (contract)

```yaml
name: <unique-within-agent>
version: <semver>
applies_to:
  source: <discord|slack|notion|github|...|any>
  kind: <message_thread|transcript|doc_edit|...>

system_prompt: |
  The full system prompt (multi-line). Includes:
  - role description
  - explicit output language rule (match input language)
  - explicit "do not invent" rule
  - explicit confidence requirement

tool_schema:                  # Anthropic tool-use schema
  name: <function name>
  description: <when to call this tool>
  input_schema:
    type: object
    properties: ...
    required: [...]

confidence_threshold: <float 0..1>   # below this, output dropped
min_body_chars: <int>               # signal threshold for body length

guards:                       # appended to system prompt
  - "explicit rule 1"
  - "explicit rule 2"

few_shot:                     # optional, included as user/assistant turns
  - input: ...
    output: ...

tenant_override:              # which fields tenants may modify
  prompt_addendum: true
  confidence_threshold:
    min: 0.5
    max: 0.95
  min_body_chars:
    min: 30
```

## Versioning + change management

- Bump `version` on any change to `system_prompt`, `tool_schema`, or `guards`.
- Store prompt version in `events.payload.distill_meta.prompt_version` when
  recording extraction output → enables audit + retroactive re-distill on
  prompt improvements.
- A/B test prompts by setting `tenant_connectors.config.distill_strategy_override`
  → use a candidate strategy for that tenant.

## Why externalize at all?

1. **Tenant tunability** without code deploys.
2. **Auditability** — exact prompt version in extraction metadata.
3. **A/B testing** — swap strategy without re-deploying agent.
4. **Translation** — non-Korean tenants may want English system prompts;
   `system_prompt_locales:` future field.
5. **Compliance** — tenant_admin can review prompts before consent (some
   enterprise customers will require this).
