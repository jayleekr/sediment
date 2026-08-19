-- Migration 008: artifact link graph (sediment#141)
--
-- Nothing in the schema records a relationship between two artifacts. There is
-- no equivalent of a wiki's [[wikilink]] or its contradiction callout, so:
--
--   1. retrieval can only find what BM25/vector matched, never what the match
--      points at;
--   2. conflicting knowledge is a loss rather than an asset. #138 stopped the
--      previous body from being erased, but a second author's differing
--      decision still had nowhere to exist AS a separate claim.
--
-- In a single-author vault a conflict is a mistake to resolve. Across people it
-- is the most valuable signal the system has, and it was the one thing the
-- pipeline reliably destroyed.
--
-- Link kinds:
--   mentions      A refers to B
--   derived_from  A was synthesized from B (evidence chain for origin='derived')
--   supports      A corroborates B
--   contradicts   A and B cannot both hold. resolved_at IS NULL = still open
--   supersedes    A replaces B; B stays readable
--
-- Direction is always src → dst. `contradicts` is conceptually symmetric but
-- stored one-way: the row records WHO raised the conflict, which is exactly
-- what a reviewer needs. Readers that want symmetry query both columns — the
-- indexes below cover each direction.

BEGIN;

CREATE TABLE IF NOT EXISTS artifact_links (
  id                 UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  src_artifact_id    UUID NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
  dst_artifact_id    UUID NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
  kind               TEXT NOT NULL CHECK (kind IN
                       ('mentions','derived_from','supports','contradicts','supersedes')),
  -- Which chunks justify the link. A contradiction nobody can trace back to
  -- specific text is unreviewable.
  evidence_chunk_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  note               TEXT,
  created_by         UUID REFERENCES members(id) ON DELETE SET NULL,
  -- Only meaningful for 'contradicts': NULL = open, set = adjudicated.
  resolved_at        TIMESTAMPTZ,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- One link of a given kind per ordered pair; re-running distill re-asserts
  -- rather than duplicating.
  UNIQUE (src_artifact_id, dst_artifact_id, kind),
  -- A self-link is always a bug, and a cheap one to make in a batch writer.
  CONSTRAINT artifact_links_no_self CHECK (src_artifact_id <> dst_artifact_id)
);

CREATE INDEX IF NOT EXISTS artifact_links_src_idx
  ON artifact_links (tenant_id, src_artifact_id, kind);
CREATE INDEX IF NOT EXISTS artifact_links_dst_idx
  ON artifact_links (tenant_id, dst_artifact_id, kind);
-- The hygiene job (sediment#143) sweeps exactly this predicate.
CREATE INDEX IF NOT EXISTS artifact_links_open_contradictions_idx
  ON artifact_links (tenant_id, created_at DESC)
  WHERE kind = 'contradicts' AND resolved_at IS NULL;

ALTER TABLE artifact_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE artifact_links FORCE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policy WHERE polrelid = 'artifact_links'::regclass
      AND polname = 'tenant_isolation'
  ) THEN
    CREATE POLICY tenant_isolation ON artifact_links
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
        'GRANT SELECT, INSERT, UPDATE, DELETE ON artifact_links TO %I', app_role);
    END IF;
  END LOOP;
END $$;

COMMIT;

INSERT INTO schema_migrations(name) VALUES ('008_artifact_links')
  ON CONFLICT (name) DO NOTHING;
