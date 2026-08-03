-- Migration 006: tenant-scoped retrieval aliases (sediment#139)
--
-- Retrieval ranking currently boosts on keyword maps hardcoded in Python:
--   lab_lib/search_utils.py  _PROJECT_HINT_MAP  ('동아일보' → donga, …)
--                            _TYPE_HINT_MAP     ('칼럼' → column, …)
--   routers/library.py       verbatim copies of both, plus a SQL LIKE chain
--                            demoting 'products/sediment/SPEC%' and friends
--
-- Every one of those is a HypeProof-workspace proper noun. Sediment is
-- multi-tenant (RLS from day 1) — for any other tenant those keywords are
-- noise at best, and at worst pull an unrelated query toward the wrong repo
-- path. Supporting a second tenant's vocabulary required a code change and a
-- deploy.
--
-- This table makes the terms data. The multipliers stay in code (they are
-- tuning constants, not tenant vocabulary); only WHAT matches moves here.
--
-- Kinds:
--   type               alias token → artifacts.type          (3x boost)
--   ref_prefix         alias token → substring of artifacts.ref (2x boost)
--   entity             alias → canonical entity name; reserved for the entity
--                      pages of sediment#140/#141, not yet read by retrieval
--   demote_ref_prefix  ref prefix to DEMOTE (0.8x). alias == target_value here;
--                      there is no query token to match, the row IS the rule.
--
-- Additive + idempotent. Seeds today's hardcoded values for the default tenant
-- so its recall is unchanged; every other tenant simply starts empty.

BEGIN;

CREATE TABLE IF NOT EXISTS tenant_aliases (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  alias        TEXT NOT NULL,
  target_kind  TEXT NOT NULL CHECK (target_kind IN
                 ('type','ref_prefix','entity','demote_ref_prefix')),
  target_value TEXT NOT NULL,
  source       TEXT NOT NULL DEFAULT 'manual'
                 CHECK (source IN ('seed','manual','learned')),
  confidence   REAL NOT NULL DEFAULT 0.5,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, alias, target_kind)
);

CREATE INDEX IF NOT EXISTS tenant_aliases_lookup_idx
  ON tenant_aliases (tenant_id, target_kind);

ALTER TABLE tenant_aliases ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_aliases FORCE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policy WHERE polrelid = 'tenant_aliases'::regclass
      AND polname = 'tenant_isolation'
  ) THEN
    CREATE POLICY tenant_isolation ON tenant_aliases
      USING (tenant_id = current_tenant_id());
  END IF;
END $$;

-- Grant to whichever app role exists (curator_app local / sediment_app prod —
-- the rename happened on prod only). Same pattern as migration 002.
DO $$
DECLARE
  app_role TEXT;
BEGIN
  FOR app_role IN SELECT unnest(ARRAY['curator_app', 'sediment_app', 'curator_service', 'sediment_service']) LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = app_role) THEN
      EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_aliases TO %I', app_role);
    END IF;
  END LOOP;
END $$;

-- ============================================================
-- Seed: today's hardcoded maps, for the default tenant only
-- ============================================================
-- Verbatim from _TYPE_HINT_MAP / _PROJECT_HINT_MAP / the library.py LIKE chain
-- as of this migration. Seeding preserves the default tenant's recall exactly;
-- without it, migrating would silently regress every golden query that leans
-- on a boost. source='seed' marks them as machine-migrated so an operator can
-- tell them from anything curated later.
INSERT INTO tenant_aliases (tenant_id, alias, target_kind, target_value, source, confidence)
SELECT t.id, v.alias, v.kind, v.target, 'seed', 1.0
FROM tenants t
CROSS JOIN (VALUES
  -- _TYPE_HINT_MAP
  ('칼럼','type','column'),
  ('column','type','column'),
  ('리서치','type','research'),
  ('research','type','research'),
  ('daily','type','research'),
  ('소설','type','novel'),
  ('novel','type','novel'),
  ('evaluation','type','research'),
  ('harness','type','research'),
  ('benchmark','type','research'),
  ('agents','type','research'),
  -- _PROJECT_HINT_MAP
  ('donga','ref_prefix','donga'),
  ('동아','ref_prefix','donga'),
  ('동아일보','ref_prefix','donga'),
  ('academy','ref_prefix','ai-architect-academy'),
  ('아카데미','ref_prefix','ai-architect-academy'),
  ('curator','ref_prefix','ai-curator'),
  ('큐레이터','ref_prefix','ai-curator'),
  ('simulacra','ref_prefix','simulacra'),
  ('시뮬라크라','ref_prefix','simulacra'),
  ('roadmap','ref_prefix','hypeproof-roadmap'),
  ('로드맵','ref_prefix','hypeproof-roadmap'),
  ('validation','ref_prefix','sediment/VALIDATION'),
  ('validator','ref_prefix','sediment/VALIDATION'),
  -- meta-doc demotion (alias == target_value; the row IS the rule)
  ('products/sediment/SPEC','demote_ref_prefix','products/sediment/SPEC'),
  ('products/sediment/README','demote_ref_prefix','products/sediment/README'),
  ('products/sediment/TEST_','demote_ref_prefix','products/sediment/TEST_'),
  ('products/sediment/DECISIONS','demote_ref_prefix','products/sediment/DECISIONS')
) AS v(alias, kind, target)
WHERE t.slug = 'hypeproof-lab'
ON CONFLICT (tenant_id, alias, target_kind) DO NOTHING;

COMMIT;

INSERT INTO schema_migrations(name) VALUES ('006_tenant_retrieval_aliases')
  ON CONFLICT (name) DO NOTHING;
