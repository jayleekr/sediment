-- Rollback for 005_derived_knowledge_layer (sediment#140).
--
-- Required by infra/migrations/README.md because 005 REPLACES the
-- artifacts_type_check constraint rather than only adding columns.
--
-- WARNING: this fails by design if any derived-only row still exists. Drop or
-- convert those first — silently deleting synthesized knowledge to satisfy a
-- rollback is worse than a loud failure.
--
--   SELECT count(*) FROM artifacts
--   WHERE type IN ('entity','concept','topic','question','comparison');

BEGIN;

ALTER TABLE artifacts DROP CONSTRAINT IF EXISTS artifacts_type_check;
ALTER TABLE artifacts ADD CONSTRAINT artifacts_type_check CHECK (type IN (
  'column','research','novel','note','decision','meeting','message','event'
));

DROP INDEX IF EXISTS artifacts_tenant_origin_idx;
DROP INDEX IF EXISTS artifacts_derived_type_idx;
DROP INDEX IF EXISTS artifacts_restricted_visibility_idx;

ALTER TABLE artifacts DROP CONSTRAINT IF EXISTS artifacts_origin_check;
ALTER TABLE artifacts DROP CONSTRAINT IF EXISTS artifacts_visibility_check;

ALTER TABLE artifacts
  DROP COLUMN IF EXISTS origin,
  DROP COLUMN IF EXISTS confidence,
  DROP COLUMN IF EXISTS synthesized_at,
  DROP COLUMN IF EXISTS visibility;

DELETE FROM schema_migrations WHERE name = '005_derived_knowledge_layer';

COMMIT;
