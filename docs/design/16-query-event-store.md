# 16 — Query Event Store

> Status: design. The structural prerequisite for [15-self-improving-rag](15-self-improving-rag.md).
> What "did the system answer well?" needs as data — schema, write paths,
> indexes, retention.

---

## 1. Why this doc exists

Phase 1 of the self-improving plan (RAGAS-in-CI) and Phases 2–5
(implicit signals → judge → auto-tuning) all depend on having a clean
join key from:

```
user query  →  router intent  →  retrieval  →  answer  →  signals (thumbs, copy, re-ask, dwell, judge)
```

Today this chain is captured in **four disconnected places**:

| What | Where now | Gap |
|---|---|---|
| Query text | `messages.content` (role=user) | OK |
| Answer text | `messages.content` (role=assistant) | OK |
| Citations | `messages.citations` (jsonb) | OK |
| Tokens (LLM) | `messages.tokens_in/out` | retrieval cost not captured |
| Conv meta | `conversations` | OK |
| API request | `mcp_call_log` | no query text, no intent, no message_id |
| User feedback | `events.kind='feedback.message'` | references message_id ✓ |
| **Router intent** | **nowhere (in-memory only)** | per-query intent lost |
| **Judge scores** | **nowhere** | needs to land somewhere |
| **Implicit signals** (re-ask, copy, dwell) | **nowhere** | sediment#15 phase 2 |

The unified store has to make these JOIN cleanly so we can ask:

> "For all `ask` queries in the last 7 days where router routed to
> `library` AND faithfulness judge score < 3 AND user did not copy
> the answer, what are the top 10 query phrasings?"

That one question is the entire self-improvement loop.

---

## 2. Schema — the additive minimum

The principle: **don't fork the data model**. Extend `messages` and add
two thin sidecar tables. Existing rows continue to work; new columns
default to NULL/empty.

### 2.1 Extend `messages` (one ALTER TABLE)

```sql
ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS intent         TEXT,
  ADD COLUMN IF NOT EXISTS intent_confidence REAL,
  ADD COLUMN IF NOT EXISTS retrieval_ms   INT,
  ADD COLUMN IF NOT EXISTS compose_ms     INT,
  ADD COLUMN IF NOT EXISTS grounding_status TEXT,
  ADD COLUMN IF NOT EXISTS grounding_valid_refs JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS grounding_invalid_refs JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS task_tag       TEXT;

CREATE INDEX IF NOT EXISTS messages_intent_idx
  ON messages (tenant_id, intent, ts DESC) WHERE archived = false;
CREATE INDEX IF NOT EXISTS messages_grounding_idx
  ON messages (tenant_id, grounding_status, ts DESC)
  WHERE archived = false AND role = 'assistant';
```

| Column | Source | Purpose |
|---|---|---|
| `intent` | langgraph router (`library / member / meta / freshness / decision`) | per-query intent for cohort analysis |
| `intent_confidence` | optional (Phase 4+ LLM classifier) | catch unsure routes for retraining |
| `retrieval_ms` | langgraph timing | retrieval-vs-LLM cost split |
| `compose_ms` | langgraph timing | spot LLM regressions |
| `grounding_status` | `_compose_grounded_answer` output | `passed / passed_after_retry / no_evidence / citation_validation_failed / freshness_deterministic` |
| `grounding_valid_refs` / `_invalid_refs` | citation validator | how grounded the answer was |
| `task_tag` | optional client header `X-Sediment-Task` | mark dogfood task type (e.g. `task=meeting-prep`) |

Only set on `role='assistant'` rows. Old rows: all-NULL, ignored by
aggregators.

### 2.2 New: `message_signals`

Per-message implicit signal log. Append-only; one row per signal.

```sql
CREATE TABLE IF NOT EXISTS message_signals (
  id          BIGSERIAL PRIMARY KEY,
  tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  message_id  UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL CHECK (kind IN (
                'thumbs_up', 'thumbs_down', 'copy', 'cite_export',
                're_ask', 'dwell', 'session_end_satisfied',
                'session_end_unsatisfied'
              )),
  value       REAL,          -- dwell sec / re-ask delta / NULL for binary
  meta        JSONB DEFAULT '{}'::jsonb,
  source      TEXT NOT NULL, -- 'web' | 'cli' | 'mcp-shim' | 'judge' | 'cron'
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS message_signals_message_idx
  ON message_signals (message_id, created_at);
CREATE INDEX IF NOT EXISTS message_signals_tenant_kind_idx
  ON message_signals (tenant_id, kind, created_at DESC);

ALTER TABLE message_signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE message_signals FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON message_signals
  USING (tenant_id = current_tenant_id());
GRANT SELECT, INSERT ON message_signals TO curator_app;
GRANT USAGE, SELECT ON SEQUENCE message_signals_id_seq TO curator_app;
```

Why separate table, not `events`? `events` is the firehose for cross-
domain things (Discord fetch, GitHub webhook, ingest). Filtering it for
message signals every query is painful. A purpose-built table lets us
index tightly on `(message_id, kind)`.

### 2.3 New: `message_judge_scores`

LLM-as-judge output. Multiple judges allowed per message (Phase 3 ships
a single faithfulness judge; future may add answer_relevancy, harm,
PII-leak).

```sql
CREATE TABLE IF NOT EXISTS message_judge_scores (
  id          BIGSERIAL PRIMARY KEY,
  tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  message_id  UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  judge       TEXT NOT NULL,  -- 'faithfulness' | 'relevancy' | 'harm' | …
  model       TEXT NOT NULL,  -- 'claude-sonnet-4-6'
  score       REAL NOT NULL,  -- normalized 0..1
  raw_score   REAL,           -- pre-normalization (e.g. 1..5 scalar)
  reason      TEXT,           -- judge's structured rationale
  rubric_version TEXT,        -- 'v1' so we can re-score on rubric changes
  cost_usd    REAL NOT NULL DEFAULT 0,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (message_id, judge, rubric_version)
);

CREATE INDEX IF NOT EXISTS message_judge_tenant_judge_idx
  ON message_judge_scores (tenant_id, judge, created_at DESC);

ALTER TABLE message_judge_scores ENABLE ROW LEVEL SECURITY;
ALTER TABLE message_judge_scores FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON message_judge_scores
  USING (tenant_id = current_tenant_id());
GRANT SELECT, INSERT ON message_judge_scores TO curator_app;
GRANT USAGE, SELECT ON SEQUENCE message_judge_scores_id_seq TO curator_app;
```

`UNIQUE (message_id, judge, rubric_version)` — re-judging an old
message under a new rubric version creates a new row. The old score
stays for time-series comparison.

### 2.4 No new tables for re-ask / dwell

These are derived signals — computed by a cron from existing data and
written as `message_signals` rows. Keeps the model normalized.

---

## 3. Write paths — who writes what

```
                ┌────────────────────────────────────────────────────────┐
                │ langgraph (sediment_langgraph/main.py)                 │
                │   ├─ insert messages (user, assistant) ── existing     │
                │   └─ NEW: also set intent, retrieval_ms, compose_ms,   │
                │           grounding_status, *_refs on assistant row    │
                └────────────────────────────────────────────────────────┘
                                       │
                                       ▼
                ┌────────────────────────────────────────────────────────┐
                │ /api/v1/feedback (routers/feedback.py)                 │
                │   THEN: writes to events.kind='feedback.message'       │
                │   NOW:  ALSO write to message_signals(kind=thumbs_…)   │
                │   (keep events writer for back-compat, dual-write)     │
                └────────────────────────────────────────────────────────┘
                                       │
                                       ▼
                ┌────────────────────────────────────────────────────────┐
                │ frontend (web copy / cite-export buttons)              │
                │   POST /api/v1/events/cite-export ── existing          │
                │   NEW: also POST /api/v1/signals/copy { message_id }   │
                │        → writes message_signals(kind=copy)             │
                └────────────────────────────────────────────────────────┘
                                       │
                                       ▼
                ┌────────────────────────────────────────────────────────┐
                │ cron: signal_derivation.py (every 15 min)              │
                │   scans last 30 min of messages, derives:              │
                │     - re_ask: same-conv duplicate user message         │
                │       within 5 min, similarity ≥ 0.7                   │
                │     - dwell: time between assistant msg and next user  │
                │       msg in same conv                                 │
                │     - session_end_(un)satisfied: 5+ min idle, then no  │
                │       re-ask AND has thumbs_up/copy = satisfied        │
                │   Writes message_signals rows                          │
                └────────────────────────────────────────────────────────┘
                                       │
                                       ▼
                ┌────────────────────────────────────────────────────────┐
                │ cron: judge_daily.py (nightly 06:00 KST)               │
                │   pulls yesterday's assistant messages,                │
                │   runs faithfulness judge, writes message_judge_scores │
                └────────────────────────────────────────────────────────┘
```

All four writers are independent; no transactional dependency between
them. A failure in one (e.g. judge crashes) does not block user queries.

---

## 4. The one query that makes it all worth it

```sql
-- "Recent failures worth turning into golden cases":
--   assistant messages from last 7 days that EITHER
--     - had grounding_status != 'passed*'
--     - OR judge faithfulness < 0.5
--     - OR got a thumbs_down
--     - OR triggered a re_ask in the same conv
--   ranked by signal severity, deduplicated on similar query text.

WITH bad_msgs AS (
  SELECT
    m.id AS message_id,
    m.conv_id,
    m.ts,
    u.content AS user_query,
    m.content AS assistant_answer,
    m.intent,
    m.grounding_status,
    COALESCE(j.score, 0)                              AS judge_score,
    COALESCE(SUM(CASE WHEN s.kind='thumbs_down' THEN 1 ELSE 0 END), 0) AS thumbs_down,
    COALESCE(SUM(CASE WHEN s.kind='re_ask'      THEN 1 ELSE 0 END), 0) AS re_asks,
    COALESCE(SUM(CASE WHEN s.kind='copy'        THEN 1 ELSE 0 END), 0) AS copies,
    COALESCE(SUM(CASE WHEN s.kind='thumbs_up'   THEN 1 ELSE 0 END), 0) AS thumbs_up
  FROM messages m
  JOIN messages u
    ON u.conv_id = m.conv_id
   AND u.role    = 'user'
   AND u.ts      < m.ts
   AND u.ts      = (
     SELECT MAX(ts) FROM messages WHERE conv_id = m.conv_id
                                     AND role   = 'user'
                                     AND ts     < m.ts
   )
  LEFT JOIN message_judge_scores j
    ON j.message_id = m.id AND j.judge = 'faithfulness'
  LEFT JOIN message_signals s
    ON s.message_id = m.id
  WHERE m.role = 'assistant'
    AND m.ts > now() - interval '7 days'
    AND m.archived = false
  GROUP BY m.id, u.content, j.score
)
SELECT
  intent,
  COUNT(*) AS bad_count,
  array_agg(DISTINCT left(user_query, 60) ORDER BY left(user_query, 60))
    FILTER (WHERE bad_count > 0)  AS sample_queries
FROM bad_msgs
WHERE
     grounding_status NOT IN ('passed', 'passed_after_retry', 'freshness_deterministic')
  OR judge_score < 0.5
  OR thumbs_down > 0
  OR (re_asks > 0 AND thumbs_up = 0 AND copies = 0)
GROUP BY intent
ORDER BY bad_count DESC;
```

This query is the feeder for `sediment learn add` (Phase 4 of doc 15).
Every row it returns is a candidate golden case.

---

## 5. Retention

| Table | Retention | Why |
|---|---|---|
| `messages` | 365 days | NPS / quarterly review |
| `message_signals` | 90 days | needed for self-improvement loop; aggregate daily into `usage_daily` after |
| `message_judge_scores` | 365 days | regression detection over quarters |
| `mcp_call_log` | 90 days | already documented (sediment#deployment doc) |
| `events` | 365 days (existing) | unchanged |

Beyond retention, aggregate to `usage_daily` (per-tenant per-day
counters) so dashboards still work.

---

## 6. Migration

```
infra/migrations/002_query_event_store.sql
```

- `ALTER TABLE messages ADD COLUMN IF NOT EXISTS …` (idempotent)
- `CREATE TABLE IF NOT EXISTS message_signals …`
- `CREATE TABLE IF NOT EXISTS message_judge_scores …`
- Grants + RLS policies as per existing pattern
- Insert into `schema_migrations`

Apply with the existing `scripts/apply_migrations.py` (same flow as
sediment#cli migration 001).

---

## 7. What this is NOT

- Not a new event firehose. `events` keeps its existing role.
- Not real-time stream processing. All derived signals run on cron
  (15 min cadence for re-ask, daily for judge).
- Not a privacy regression — message bodies were already stored. New
  columns are metadata about already-stored content. RLS unchanged.
- Not append-only. Judge scores re-version (UNIQUE on
  `(message_id, judge, rubric_version)`); user can also delete a
  conversation, which cascades to all signals/scores.

---

## 8. Open decisions

| # | Question | Default |
|---|---|---|
| 1 | Should `intent` be a constrained enum or free TEXT? | TEXT for now — Phase 4 may add LLM classifier with novel intents |
| 2 | Should `message_signals` allow multiple thumbs_up per user (one per click)? | Yes — track click events, dedupe at aggregation time |
| 3 | Judge scores by tenant or global model? | Tenant — different tenants may judge differently (PII tolerance varies) |
| 4 | Frontend writes signals via REST or batched via a separate endpoint? | REST per-signal; cheap, no batching needed at our scale |
| 5 | Re-ask similarity threshold? | 0.7 cosine on embeddings; tunable via `lab_lib/settings.py` |

---

## 9. Sequence

Tightly coupled to [15-self-improving-rag](15-self-improving-rag.md):

1. **Week 0 (prereq)**: ship migration 002. All new columns NULL/empty
   on existing rows; nothing else breaks.
2. **Week 1**: langgraph writes `intent`, `retrieval_ms`, `compose_ms`,
   `grounding_*` on every assistant message. Unblocks RAGAS-in-CI
   (Phase 1 of doc 15) joining intent.
3. **Week 2**: `/api/v1/signals/copy` endpoint + `message_signals`
   dual-write from feedback. Frontend wires the copy button per
   sediment#15 Phase 2.
4. **Week 3**: `signal_derivation.py` cron writes re_ask + dwell.
5. **Week 4**: `judge_daily.py` cron writes `message_judge_scores`.
6. **Week 5+**: The query in §4 powers `sediment learn add` (doc 15
   Phase 4) and the orbit Collect step.

---

*Last updated: 2026-05-22. Sits next to [15-self-improving-rag](15-self-improving-rag.md) — that doc owns the LOOP, this doc owns the STORE.*
