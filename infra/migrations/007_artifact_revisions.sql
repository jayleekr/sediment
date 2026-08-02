-- Migration 007: append-only artifact revisions (sediment#138)
--
-- The bug this closes
-- -------------------
-- `scripts/distill.py` derives an artifact ref from the decision's topic slug
-- (`decision/<slug>`), and the ingester upserts on (tenant_id, ref) with
-- `body = EXCLUDED.body`. `_decision_markdown`'s docstring states the intent —
-- "re-distilling the same decision UPDATES, never duplicates" — but two
-- DIFFERENT decisions that slug alike collide, and the second one erases the
-- first: body, source, rationale, all of it. Chunks are deleted wholesale and
-- rebuilt, so retrieval loses it too.
--
-- One author re-deciding is an update. Two authors deciding differently is not,
-- and a knowledge layer meant to accumulate across people cannot treat them the
-- same. Nothing anywhere recorded that the first version had existed.
--
-- What this adds
-- --------------
--   artifact_revisions   append-only body history with author + source
--   artifacts.rev        monotonic counter for optimistic concurrency
--
-- Distill workers, the ingester and the chat path all write artifacts, and
-- nothing serialized them. `rev` makes a lost update detectable instead of
-- silent.
--
-- This migration does NOT decide what a conflicting write MEANS (reinforce /
-- supersede / contradict). That judgment needs the link graph — sediment#141.
-- Preserving the prior text is the prerequisite, and it is what makes the
-- decision recoverable later rather than lost now.
--
-- Additive + idempotent. Existing artifacts start at rev=1 with no history;
-- their first update from here on records the body they have today.

BEGIN;

ALTER TABLE artifacts
  ADD COLUMN IF NOT EXISTS rev INT NOT NULL DEFAULT 1;

COMMENT ON COLUMN artifacts.rev IS
  'Monotonic revision counter. Bumped on every body change; used for optimistic '
  'concurrency between distill / ingester / chat writers (sediment#138).';

CREATE TABLE IF NOT EXISTS artifact_revisions (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  artifact_id  UUID NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
  -- The rev this row's body WAS. artifacts.rev is always > every rev here.
  rev          INT NOT NULL,
  body         TEXT,
  frontmatter  JSONB NOT NULL DEFAULT '{}'::jsonb,
  -- Who/what produced the superseded body. author_id is the artifact's author
  -- at that time; source_ref names the producing run (e.g. 'distill:conv/<id>')
  -- so a revision is traceable to the conversation or event batch behind it.
  author_id    UUID REFERENCES members(id) ON DELETE SET NULL,
  source_ref   TEXT,
  replaced_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (artifact_id, rev)
);

CREATE INDEX IF NOT EXISTS artifact_revisions_artifact_idx
  ON artifact_revisions (artifact_id, rev DESC);
CREATE INDEX IF NOT EXISTS artifact_revisions_tenant_time_idx
  ON artifact_revisions (tenant_id, replaced_at DESC);

ALTER TABLE artifact_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE artifact_revisions FORCE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policy WHERE polrelid = 'artifact_revisions'::regclass
      AND polname = 'tenant_isolation'
  ) THEN
    CREATE POLICY tenant_isolation ON artifact_revisions
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
        'GRANT SELECT, INSERT, UPDATE, DELETE ON artifact_revisions TO %I', app_role);
    END IF;
  END LOOP;
END $$;

COMMIT;

INSERT INTO schema_migrations(name) VALUES ('007_artifact_revisions')
  ON CONFLICT (name) DO NOTHING;
