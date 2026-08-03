-- Migration 009: context packs (sediment#142)
--
-- A wiki keeps a ~500-word `hot.md` and a full `index.md` so any session can
-- pick up recent context without crawling the vault. Sediment had no
-- equivalent: a new session either ran a vector search (which answers a
-- question it does not yet know to ask) or read nothing.
--
-- The file version assumes one owner. `hot.md` is whatever the single author
-- last touched. Sediment is multi-member, so a pack needs a SCOPE:
--
--   'tenant'          shared context — what the workspace as a whole is doing
--   'member:<uuid>'   one person's, filtered to what THEY may read (#140)
--   'domain:<slug>'   one topic area
--
-- Packs are derived data. Losing the table costs a regeneration, not knowledge,
-- so there is no history and no revision tracking here.

BEGIN;

CREATE TABLE IF NOT EXISTS context_packs (
  tenant_id      UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  scope_key      TEXT NOT NULL,
  kind           TEXT NOT NULL CHECK (kind IN ('hot','index')),
  body           TEXT NOT NULL,
  -- Rough token count. The whole value of a pack is being cheap enough to read
  -- unconditionally; a pack that silently grew past its budget has stopped
  -- being one, and the hygiene job (sediment#143) can see that here.
  token_estimate INT NOT NULL DEFAULT 0,
  -- What the pack was built from, for debugging a stale or empty one.
  sources        JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, scope_key, kind)
);

CREATE INDEX IF NOT EXISTS context_packs_staleness_idx
  ON context_packs (tenant_id, updated_at);

ALTER TABLE context_packs ENABLE ROW LEVEL SECURITY;
ALTER TABLE context_packs FORCE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policy WHERE polrelid = 'context_packs'::regclass
      AND polname = 'tenant_isolation'
  ) THEN
    CREATE POLICY tenant_isolation ON context_packs
      USING (tenant_id = current_tenant_id());
  END IF;
END $$;

DO $$
DECLARE
  app_role TEXT;
BEGIN
  FOR app_role IN SELECT unnest(ARRAY['curator_app', 'sediment_app',
                                      'curator_service', 'sediment_service']) LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = app_role) THEN
      EXECUTE format(
        'GRANT SELECT, INSERT, UPDATE, DELETE ON context_packs TO %I', app_role);
    END IF;
  END LOOP;
END $$;

COMMIT;

INSERT INTO schema_migrations(name) VALUES ('009_context_packs')
  ON CONFLICT (name) DO NOTHING;
