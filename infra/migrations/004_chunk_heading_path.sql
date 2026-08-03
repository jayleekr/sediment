-- Migration 004: persist chunk heading_path (sediment#137)
--
-- `lab_lib.chunker` already computes a heading_path ("Section A > Subsection 1")
-- for every chunk and WO-7 (2026-05-23) fixed a provenance bug in how it is
-- derived — but the ingester never stored it, so the value was discarded at the
-- INSERT and citations could only point at "somewhere in this document".
--
-- Additive + idempotent. Existing rows get NULL and are backfilled by
-- re-ingestion (`scripts/reingest_to.sh`); every reader must tolerate NULL.

BEGIN;

ALTER TABLE chunks
  ADD COLUMN IF NOT EXISTS heading_path TEXT;

COMMENT ON COLUMN chunks.heading_path IS
  'Heading breadcrumb of the source section, e.g. "Design > Retrieval". '
  'NULL for rows ingested before sediment#137 — readers must tolerate NULL.';

COMMIT;

-- Self-record so apply_migrations.py skips us on the next run.
-- Mirrors the pattern in 001-003 (apply_migrations doesn't track this itself —
-- each migration is responsible for its own bookkeeping row).
INSERT INTO schema_migrations(name) VALUES ('004_chunk_heading_path')
  ON CONFLICT (name) DO NOTHING;
