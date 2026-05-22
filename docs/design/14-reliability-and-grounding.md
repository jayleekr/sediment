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
- `test_claim_grounding.py` evaluates sentence-level factual claims against the
  specific cited snippets.
- `test_vault_freshness_contract.py` locks the source-level freshness axes and
  the cron `vault.sync` breadcrumb.
- Existing `P2-GROUND-01` continues to run live SSE grounding probes.
- `P2-GROUND-03` is a provider-free claim-grounding contract. It reports
  per-claim JSON with `supported`, `partially_supported`, `unsupported`, and
  `not_factual` verdicts. The optional LLM judge path is opt-in via
  `SEDIMENT_CLAIM_LLM_JUDGE=1`; offline runs skip it explicitly instead of
  pretending a model judge ran.

## 6. Decision Provenance

Distilled decision artifacts carry machine-readable provenance in frontmatter:

- `provenance.kind`: `conversation`, `discord_events`, or `unknown`
- `provenance.source_ref` and `provenance.source_title`
- `provenance.source_event_ids` for Discord event batches
- `provenance.source_message_ids` for source messages where available
- `provenance.channel`, `source_date`, and source window timestamps where
  available

Retrieval returns this frontmatter as `provenance` and, for decision artifacts,
also emits `decision_provenance`. If a decision artifact lacks provenance, the
payload includes `decision_provenance.missing=true` so the UI can show a
warning instead of silently treating the distilled artifact as a primary
source.

## 7. Daily Reliability Monitor

`python -m validator.checks.reliability_daily` emits a stable JSON report to
stdout and writes `output/reliability/<YYYY-MM-DD>-<tenant>.json`.

The monitor is deterministic by default:

- **Freshness:** latest vault sync, artifact update, chunk update, decision
  timestamp, artifact/chunk counts, and artifact-without-chunks violations.
- **Recall:** latest local `P1-latest.json` golden recall results when present;
  otherwise the section is marked unavailable rather than fabricated.
- **Grounding:** citation hard gate, zero-evidence fail-closed contract, and
  claim-level support contract.
- **Distill:** Discord events seen, decisions/actions extracted, decision
  artifacts created, and decisions linked to `source_artifact_id`.

If the DB is unavailable, the report still emits with `status=degraded` and
explicit warnings. The default CLI exit code remains `0` so cron can always
produce an artifact; use `--strict-exit` in CI to fail on degraded/critical
status.

Scheduler integration lives in `config/cron.yaml` under `reliability_daily`.
By default it runs at 08:30 KST and sends a `reliability.daily` notification
only when warnings exist. The notification payload contains `status`,
`warning_count`, `critical_count`, `major_count`, `report_path`, and the top
warnings list, which matches the existing `scripts/notify` route model.

## 8. Boundary

The chat path still does not mutate vault state. It may write:

- assistant `messages`
- query `events`
- LLM call accounting

It must not trigger ingest, connector sync, distill, or notifications.
