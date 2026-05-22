-- Migration 001: CLI multi-user access
-- Adds:
--   1. members.revoked_at  — kill-switch for JWTs without rotating secret
--   2. device_authorization_codes — RFC 8628 device flow state
--   3. mcp_call_log         — per-call audit (member, tool, latency)
-- All additive + idempotent. Safe to re-apply.

BEGIN;

-- 1. revoked_at on members (kill-switch)
ALTER TABLE members
  ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ NULL;

CREATE INDEX IF NOT EXISTS members_revoked_at_idx
  ON members (revoked_at) WHERE revoked_at IS NOT NULL;

-- 2. Device authorization codes (RFC 8628)
-- The flow:
--   CLI calls /oauth-device/start                → row inserted, status='pending'
--   CLI polls /oauth-device/poll w/ device_code  → 400 authorization_pending until approved
--   User visits verification_uri, enters user_code, signs in with GitHub
--   Web UI calls /oauth-device/approve (auth req) → status='approved', member_id set
--   Next poll → 200 with JWT
CREATE TABLE IF NOT EXISTS device_authorization_codes (
  id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  device_code       TEXT NOT NULL UNIQUE,
  user_code         TEXT NOT NULL UNIQUE,
  client_id         TEXT NOT NULL,
  scope             TEXT NOT NULL DEFAULT 'default',
  status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending','approved','denied','expired')),
  member_id         UUID NULL REFERENCES members(id) ON DELETE CASCADE,
  -- approval-time snapshot so the JWT can be minted from this row without a
  -- second members lookup if member_id is later renamed
  approved_email    TEXT,
  poll_interval_s   INT NOT NULL DEFAULT 5,
  expires_at        TIMESTAMPTZ NOT NULL,
  last_polled_at    TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  approved_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS device_auth_user_code_idx
  ON device_authorization_codes (user_code) WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS device_auth_expires_idx
  ON device_authorization_codes (expires_at) WHERE status = 'pending';

-- Not a tenant-scoped table (a device code has no tenant until approved),
-- so no RLS policy. Access is gated at the API layer.

-- 3. MCP call log (per-call audit)
CREATE TABLE IF NOT EXISTS mcp_call_log (
  id            BIGSERIAL PRIMARY KEY,
  tenant_id     UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  member_id     UUID NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  tool          TEXT NOT NULL,
  client        TEXT,                       -- 'cli' | 'web' | 'mcp-shim' | NULL
  latency_ms    INT  NOT NULL,
  result_bytes  INT  NOT NULL DEFAULT 0,
  status_code   INT  NOT NULL DEFAULT 200,
  error_reason  TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS mcp_call_log_tenant_time_idx
  ON mcp_call_log (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS mcp_call_log_member_time_idx
  ON mcp_call_log (member_id, created_at DESC);

-- RLS on mcp_call_log — same pattern as artifacts/chunks.
ALTER TABLE mcp_call_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE mcp_call_log FORCE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policy WHERE polrelid = 'mcp_call_log'::regclass
      AND polname = 'tenant_isolation'
  ) THEN
    CREATE POLICY tenant_isolation ON mcp_call_log
      USING (tenant_id = current_tenant_id());
  END IF;
END $$;

-- Grants (app role: read-only; service role: full via BYPASSRLS)
GRANT SELECT, INSERT ON mcp_call_log TO curator_app;
GRANT USAGE, SELECT ON SEQUENCE mcp_call_log_id_seq TO curator_app;

-- Bookkeeping
CREATE TABLE IF NOT EXISTS schema_migrations (
  name        TEXT PRIMARY KEY,
  applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO schema_migrations(name) VALUES ('001_cli_multiuser')
  ON CONFLICT (name) DO NOTHING;

COMMIT;
