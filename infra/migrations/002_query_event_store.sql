-- Migration 002: Query event store for self-improving RAG (sediment#15, #16)
--
-- Adds:
--   1. messages.intent / intent_confidence / retrieval_ms / compose_ms /
--      grounding_status / grounding_valid_refs / grounding_invalid_refs / task_tag
--   2. message_signals (thumbs/copy/cite_export/re_ask/dwell/session_end_*)
--   3. message_judge_scores (faithfulness/relevancy/...) with rubric version
--
-- All additive + idempotent. Existing rows have NULL/empty defaults.

BEGIN;

-- ============================================================
-- 1. Extend messages with intent + timing + grounding columns
-- ============================================================

ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS intent              TEXT,
  ADD COLUMN IF NOT EXISTS intent_confidence   REAL,
  ADD COLUMN IF NOT EXISTS retrieval_ms        INT,
  ADD COLUMN IF NOT EXISTS compose_ms          INT,
  ADD COLUMN IF NOT EXISTS grounding_status    TEXT,
  ADD COLUMN IF NOT EXISTS grounding_valid_refs   JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS grounding_invalid_refs JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS task_tag            TEXT;

-- Cohort indexes — these only matter for assistant rows that have an intent.
CREATE INDEX IF NOT EXISTS messages_intent_idx
  ON messages (tenant_id, intent, ts DESC)
  WHERE archived = false AND intent IS NOT NULL;

CREATE INDEX IF NOT EXISTS messages_grounding_status_idx
  ON messages (tenant_id, grounding_status, ts DESC)
  WHERE archived = false AND role = 'assistant' AND grounding_status IS NOT NULL;

-- ============================================================
-- 2. message_signals — per-message implicit/explicit user signals
-- ============================================================

CREATE TABLE IF NOT EXISTS message_signals (
  id          BIGSERIAL PRIMARY KEY,
  tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  message_id  UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL CHECK (kind IN (
                'thumbs_up', 'thumbs_down', 'copy', 'cite_export',
                're_ask', 'dwell', 'session_end_satisfied',
                'session_end_unsatisfied'
              )),
  value       REAL,          -- dwell seconds, re-ask delta seconds, or NULL for binary
  meta        JSONB NOT NULL DEFAULT '{}'::jsonb,
  source      TEXT NOT NULL, -- 'web' | 'cli' | 'mcp-shim' | 'judge' | 'cron'
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS message_signals_message_kind_idx
  ON message_signals (message_id, kind, created_at);
CREATE INDEX IF NOT EXISTS message_signals_tenant_kind_time_idx
  ON message_signals (tenant_id, kind, created_at DESC);

ALTER TABLE message_signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE message_signals FORCE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policy WHERE polrelid = 'message_signals'::regclass
      AND polname = 'tenant_isolation'
  ) THEN
    CREATE POLICY tenant_isolation ON message_signals
      USING (tenant_id = current_tenant_id());
  END IF;
END $$;

-- Grant to whichever app role exists (curator_app for local Docker, sediment_app
-- for prod Supabase — the curator→sediment rename happened on prod only).
DO $$
DECLARE
  app_role TEXT;
BEGIN
  FOR app_role IN SELECT unnest(ARRAY['curator_app', 'sediment_app']) LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = app_role) THEN
      EXECUTE format('GRANT SELECT, INSERT ON message_signals TO %I', app_role);
      EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE message_signals_id_seq TO %I', app_role);
    END IF;
  END LOOP;
END $$;

-- ============================================================
-- 3. message_judge_scores — LLM-as-judge output per message
-- ============================================================

CREATE TABLE IF NOT EXISTS message_judge_scores (
  id              BIGSERIAL PRIMARY KEY,
  tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  message_id      UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  judge           TEXT NOT NULL,   -- 'faithfulness' | 'relevancy' | 'harm' | 'pii'
  model           TEXT NOT NULL,   -- 'claude-sonnet-4-6' | etc.
  score           REAL NOT NULL,   -- normalized 0..1
  raw_score       REAL,            -- pre-normalization (e.g. 1..5)
  reason          TEXT,            -- structured rationale from the judge
  rubric_version  TEXT NOT NULL,   -- 'v1' — bump to re-score history
  cost_usd        REAL NOT NULL DEFAULT 0,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (message_id, judge, rubric_version)
);

CREATE INDEX IF NOT EXISTS message_judge_tenant_judge_time_idx
  ON message_judge_scores (tenant_id, judge, created_at DESC);

ALTER TABLE message_judge_scores ENABLE ROW LEVEL SECURITY;
ALTER TABLE message_judge_scores FORCE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policy WHERE polrelid = 'message_judge_scores'::regclass
      AND polname = 'tenant_isolation'
  ) THEN
    CREATE POLICY tenant_isolation ON message_judge_scores
      USING (tenant_id = current_tenant_id());
  END IF;
END $$;

DO $$
DECLARE
  app_role TEXT;
BEGIN
  FOR app_role IN SELECT unnest(ARRAY['curator_app', 'sediment_app']) LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = app_role) THEN
      EXECUTE format('GRANT SELECT, INSERT ON message_judge_scores TO %I', app_role);
      EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE message_judge_scores_id_seq TO %I', app_role);
    END IF;
  END LOOP;
END $$;

-- ============================================================
-- bookkeeping
-- ============================================================

INSERT INTO schema_migrations(name) VALUES ('002_query_event_store')
  ON CONFLICT (name) DO NOTHING;

COMMIT;
