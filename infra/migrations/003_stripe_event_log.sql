-- WO-1 2026-05-23: Stripe webhook idempotency table.
--
-- Stripe retries event delivery for up to 3 days on any non-2xx response. A
-- network hiccup that drops our 200 ACK causes the same event to be replayed.
-- Without dedup, a single network blip = duplicate state flip (tenant goes
-- cancelled then cancelled again, etc.).
--
-- This table is INTENTIONALLY cross-tenant. The Stripe webhook handler runs
-- under service_session (BYPASSRLS) — no RLS policy here. The event_id is
-- the natural primary key; if INSERT collides we return deduped=true.
--
-- Apply order:
--   psql $DATABASE_URL_SERVICE < infra/migrations/003_stripe_event_log.sql
--
-- Idempotent: CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS stripe_event_log (
    event_id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- For occasional ops queries ("what did Stripe send us in the last hour?").
-- Not load-bearing for the hot path (event_id PK is sufficient for dedup).
CREATE INDEX IF NOT EXISTS stripe_event_log_received_at_idx
    ON stripe_event_log (received_at);

-- Optional: retention. Stripe's 3-day retry window means anything older than
-- ~7 days will never replay. Truncate by hand or via a daily cron later.
-- Not enforced here.

-- Self-record so apply_migrations.py skips us on the next run.
-- Mirrors the pattern in 001 + 002 (apply_migrations doesn't track this
-- itself — each migration is responsible for its own bookkeeping row).
INSERT INTO schema_migrations(name) VALUES ('003_stripe_event_log')
  ON CONFLICT (name) DO NOTHING;
