# 14 — Reliability & Grounding

> **One-line:** Sediment must fail closed when evidence is missing, expose
> freshness per pipeline stage, and make citation validity measurable in
> runtime events and validator checks.

## 1. Contract

Sediment is reliable only when three contracts hold at the same time:

1. **Freshness:** capture, artifact indexing, chunk indexing, and decision
   distillation each expose their own timestamp. A single "vault updated"
   value is not enough.
2. **Citation validity:** a generated answer may only cite indexes that were
   emitted as SSE `citation` events for that same answer.
3. **Fail closed:** when retrieval returns no citations, the chat runtime must
   return a deterministic no-evidence answer instead of asking the LLM to
   improvise.

## 2. Freshness Axes

`GET /api/v1/vault/freshness` returns backwards-compatible fields for the UI
badge plus a `signals` object:

| Signal | Meaning | Source |
|---|---|---|
| `vault_sync` | latest webhook/cron vault sync breadcrumb | `events.kind IN ('vault.ingest','vault.sync')` |
| `github_event` | latest captured GitHub event | `events.source='github'` |
| `discord_event` | latest captured Discord event | `events.source='discord'` |
| `artifact_update` | latest artifact upsert | `artifacts.updated_at` |
| `chunk_update` | latest retrievable chunk write | `chunks.created_at` |
| `decision` | latest structured decision row | `decisions.created_at` |
| `decision_artifact` | latest citable decision artifact | `artifacts.type='decision'` |

`violations[]` is the operational signal. For example,
`artifact_without_chunks` means ingest wrote artifacts but retrieval cannot
cite them.

## 3. Sync Breadcrumbs

Both ingestion paths must emit freshness breadcrumbs:

- `/webhook/ingest` writes `source='github', kind='vault.ingest'`
- `scripts.github_repo_fetch` writes `source='github', kind='vault.sync'`

The cron path emits `vault.sync` even when no files changed. That distinguishes
"the connector ran and found no work" from "the connector stopped running."

## 4. Runtime Citation Gate

The LangGraph SSE path validates model output before streaming answer deltas.

Rules:

- `len(citations) == 0` -> skip LLM and return deterministic no-evidence text.
- Answer must include at least one valid inline citation `[N]`.
- `N` must be within `1 <= N <= len(citations)`.
- Invalid refs such as `[9]` when only two citations exist fail validation.
- One strict retry is allowed; if retry fails, return a deterministic citation
  failure answer.

Grounding metadata is stored in the query event payload:

```json
{
  "grounding": {
    "status": "passed | passed_after_retry | no_evidence | citation_validation_failed",
    "citation_count": 3,
    "inline_refs": [1, 2],
    "valid_refs": [1, 2],
    "invalid_refs": [],
    "retry_count": 0
  }
}
```

## 5. Validator Coverage

The first reliability slice is deterministic:

- `test_grounding_runtime.py` covers citation index parsing and deterministic
  fail-closed answers.
- `test_vault_freshness_contract.py` locks the source-level freshness axes and
  the cron `vault.sync` breadcrumb.
- Existing `P2-GROUND-01` continues to run live SSE grounding probes.

Future work under Epic #22:

- Claim-level support scoring.
- Decision artifact provenance back to source events.
- Daily reliability report and notification route.

## 6. Boundary

The chat path still does not mutate vault state. It may write:

- assistant `messages`
- query `events`
- LLM call accounting

It must not trigger ingest, connector sync, distill, or notifications.
