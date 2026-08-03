-- Migration 005: derived knowledge layer + visibility (sediment#140)
--
-- Sediment stores raw documents (`artifacts`) and mechanical splits (`chunks`).
-- There is no layer for knowledge the system itself synthesized — the one
-- exception being `scripts/distill.py`, which writes decisions back as citable
-- artifacts. This migration generalizes that pattern.
--
--   origin         'raw' (ingested source) vs 'derived' (synthesized by us)
--   confidence     how much to trust a derived page (NULL for raw)
--   synthesized_at when the derived page was produced (NULL for raw)
--   visibility     intra-tenant access scope
--
-- WHY visibility ships in the SAME migration as origin:
-- a derived page is composed from several sources that may not share an
-- audience. RLS enforces the TENANT boundary only — it has nothing to say
-- about boundaries *inside* a tenant. So synthesis is itself a disclosure
-- path, and the rule "a derived page inherits the most restrictive visibility
-- among its sources" has to exist BEFORE derived pages accumulate. Applying it
-- retroactively would mean re-deriving every page from sources whose
-- visibility at synthesis time is no longer known.
--
-- Additive + idempotent. Every existing row becomes origin='raw',
-- visibility='tenant' — i.e. exactly today's behaviour.
--
-- Paired rollback: 005_derived_knowledge_layer.rollback.sql (the type CHECK
-- constraint is replaced, not merely added — see below).

BEGIN;

-- ============================================================
-- 1. Columns
-- ============================================================
ALTER TABLE artifacts
  ADD COLUMN IF NOT EXISTS origin         TEXT NOT NULL DEFAULT 'raw',
  ADD COLUMN IF NOT EXISTS confidence     REAL,
  ADD COLUMN IF NOT EXISTS synthesized_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS visibility     TEXT NOT NULL DEFAULT 'tenant';

-- CHECK constraints added separately so re-running is safe (ADD COLUMN IF NOT
-- EXISTS skips the column but would also skip an inline CHECK).
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'artifacts_origin_check') THEN
    ALTER TABLE artifacts ADD CONSTRAINT artifacts_origin_check
      CHECK (origin IN ('raw','derived'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'artifacts_visibility_check') THEN
    -- Ordered ladder, most restrictive first: private < tenant.
    -- Keep in sync with lab_lib/visibility.py::VISIBILITY_LADDER.
    ALTER TABLE artifacts ADD CONSTRAINT artifacts_visibility_check
      CHECK (visibility IN ('private','tenant'));
  END IF;
END $$;

COMMENT ON COLUMN artifacts.origin IS
  '''raw'' = ingested source document; ''derived'' = synthesized by Sediment.';
COMMENT ON COLUMN artifacts.visibility IS
  'Intra-tenant scope. Derived pages inherit the most restrictive visibility '
  'among their sources — see lab_lib/visibility.py (sediment#140).';

-- ============================================================
-- 2. Widen the type CHECK for knowledge page types
-- ============================================================
-- init.sql pins artifacts.type to source-document types. Derived pages need
-- wiki-style types. The old constraint is REPLACED (hence the rollback file).
DO $$
DECLARE
  con_name TEXT;
BEGIN
  SELECT conname INTO con_name
  FROM pg_constraint
  WHERE conrelid = 'artifacts'::regclass
    AND contype = 'c'
    AND pg_get_constraintdef(oid) ILIKE '%type%'
    AND pg_get_constraintdef(oid) ILIKE '%column%'
  LIMIT 1;

  -- The inline CREATE TABLE constraint is conventionally named
  -- artifacts_type_check; fall back to that when the heuristic above misses.
  IF con_name IS NULL THEN
    SELECT conname INTO con_name FROM pg_constraint
    WHERE conrelid = 'artifacts'::regclass AND conname = 'artifacts_type_check';
  END IF;

  IF con_name IS NOT NULL THEN
    EXECUTE format('ALTER TABLE artifacts DROP CONSTRAINT %I', con_name);
  END IF;

  ALTER TABLE artifacts ADD CONSTRAINT artifacts_type_check CHECK (type IN (
    -- raw source types (unchanged, from init.sql)
    'column','research','novel','note','decision','meeting','message','event',
    -- derived knowledge page types (sediment#140)
    'entity','concept','topic','question','comparison'
  ));
END $$;

-- ============================================================
-- 3. Indexes
-- ============================================================
-- Retrieval wants to reach the derived layer first, and knowledge-hygiene jobs
-- (sediment#143) sweep derived pages by recency.
CREATE INDEX IF NOT EXISTS artifacts_tenant_origin_idx
  ON artifacts (tenant_id, origin, updated_at DESC);

CREATE INDEX IF NOT EXISTS artifacts_derived_type_idx
  ON artifacts (tenant_id, type, synthesized_at DESC)
  WHERE origin = 'derived';

-- Visibility filters are applied on every read path; only non-default rows
-- are worth indexing.
CREATE INDEX IF NOT EXISTS artifacts_restricted_visibility_idx
  ON artifacts (tenant_id, author_id)
  WHERE visibility <> 'tenant';

COMMIT;

INSERT INTO schema_migrations(name) VALUES ('005_derived_knowledge_layer')
  ON CONFLICT (name) DO NOTHING;
