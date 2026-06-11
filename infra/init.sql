-- Sediment — Postgres init
-- Multi-tenant schema with Row-Level Security from day 1.
-- Single DB, single schema, tenant_id on every table.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- Roles
-- ============================================================
-- service role: bypasses RLS (used by ingest, cron, admin tasks)
-- app role: subject to RLS (used by request handlers)
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'curator_app') THEN
    CREATE ROLE curator_app NOINHERIT LOGIN PASSWORD 'curator_app_local';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'curator_service') THEN
    CREATE ROLE curator_service NOINHERIT LOGIN PASSWORD 'curator_service_local' BYPASSRLS;
  END IF;
END $$;

GRANT CONNECT ON DATABASE curator TO curator_app, curator_service;
GRANT USAGE ON SCHEMA public TO curator_app, curator_service;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO curator_app, curator_service;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO curator_app, curator_service;

-- ============================================================
-- Tenants (root scope)
-- ============================================================
CREATE TABLE IF NOT EXISTS tenants (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  slug         TEXT UNIQUE NOT NULL,
  display_name TEXT NOT NULL,
  domain       TEXT,
  custom_domain TEXT,
  plan         TEXT NOT NULL DEFAULT 'free' CHECK (plan IN ('free','pro','business','enterprise')),
  region       TEXT NOT NULL DEFAULT 'us' CHECK (region IN ('us','kr','eu')),
  status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','trialing','cancelled')),
  branding     JSONB NOT NULL DEFAULT '{}'::jsonb,
  feature_flags JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS subscriptions (
  id                       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id                UUID NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE,
  stripe_subscription_id   TEXT,
  stripe_customer_id       TEXT,
  seat_count               INT NOT NULL DEFAULT 3,
  query_quota_per_month    INT NOT NULL DEFAULT 1000,
  storage_quota_gb         INT NOT NULL DEFAULT 1,
  current_period_end       TIMESTAMPTZ,
  created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS integrations (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  kind         TEXT NOT NULL CHECK (kind IN ('discord','slack','notion','drive','github')),
  config       JSONB NOT NULL DEFAULT '{}'::jsonb,
  last_sync_at TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- Members (tenant-scoped)
-- ============================================================
CREATE TABLE IF NOT EXISTS members (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  external_id  TEXT,                 -- e.g., Discord ID
  github_login TEXT,                 -- GitHub OAuth login (SSO identity, email-independent)
  email        TEXT,
  display_name TEXT NOT NULL,
  real_name    TEXT,
  role         TEXT NOT NULL DEFAULT 'creator' CHECK (role IN ('admin','creator','viewer')),
  title        TEXT,
  expertise    JSONB NOT NULL DEFAULT '[]'::jsonb,
  interests    JSONB NOT NULL DEFAULT '[]'::jsonb,
  avatar_url   TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, email),
  UNIQUE (tenant_id, external_id),
  UNIQUE (tenant_id, github_login)
);
CREATE INDEX IF NOT EXISTS members_tenant_idx ON members(tenant_id);

-- ============================================================
-- Artifacts + Chunks
-- ============================================================
CREATE TABLE IF NOT EXISTS artifacts (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  ref          TEXT NOT NULL,        -- e.g., "research/daily/2026-04-15.md"
  type         TEXT NOT NULL CHECK (type IN ('column','research','novel','note','decision','meeting','message','event')),
  author_id    UUID REFERENCES members(id) ON DELETE SET NULL,
  date         DATE,
  slug         TEXT,
  lang         TEXT,                 -- 'ko' | 'en'
  frontmatter  JSONB NOT NULL DEFAULT '{}'::jsonb,
  body         TEXT,
  status       TEXT NOT NULL DEFAULT 'published',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, ref)
);
CREATE INDEX IF NOT EXISTS artifacts_tenant_idx ON artifacts(tenant_id);
CREATE INDEX IF NOT EXISTS artifacts_tenant_type_date_idx ON artifacts(tenant_id, type, date DESC);
CREATE INDEX IF NOT EXISTS artifacts_tenant_author_idx ON artifacts(tenant_id, author_id);
CREATE INDEX IF NOT EXISTS artifacts_frontmatter_gin ON artifacts USING gin (frontmatter jsonb_path_ops);

CREATE TABLE IF NOT EXISTS chunks (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  artifact_id  UUID NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
  seq          INT NOT NULL,
  content      TEXT NOT NULL,
  tsv          tsvector,
  embedding    vector(1536),         -- OpenAI text-embedding-3-small
  boost        REAL NOT NULL DEFAULT 0,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chunks_tenant_idx ON chunks(tenant_id);
CREATE INDEX IF NOT EXISTS chunks_artifact_idx ON chunks(artifact_id);
CREATE INDEX IF NOT EXISTS chunks_tsv_gin ON chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

-- Trigger to maintain tsv
CREATE OR REPLACE FUNCTION chunks_tsv_update() RETURNS trigger AS $$
BEGIN
  NEW.tsv := to_tsvector('simple', coalesce(NEW.content, ''));
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS chunks_tsv_trigger ON chunks;
CREATE TRIGGER chunks_tsv_trigger BEFORE INSERT OR UPDATE OF content ON chunks
FOR EACH ROW EXECUTE FUNCTION chunks_tsv_update();

-- ============================================================
-- Conversations + Messages
-- ============================================================
CREATE TABLE IF NOT EXISTS conversations (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  user_id      UUID REFERENCES members(id) ON DELETE SET NULL,
  title        TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS conversations_tenant_user_idx ON conversations(tenant_id, user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  conv_id      UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role         TEXT NOT NULL CHECK (role IN ('user','assistant','tool','system')),
  content      TEXT NOT NULL,
  citations    JSONB NOT NULL DEFAULT '[]'::jsonb,
  tokens_in    INT NOT NULL DEFAULT 0,
  tokens_out   INT NOT NULL DEFAULT 0,
  archived     BOOLEAN NOT NULL DEFAULT false,
  ts           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS messages_tenant_conv_idx ON messages(tenant_id, conv_id, ts);
CREATE INDEX IF NOT EXISTS messages_tenant_archived_idx ON messages(tenant_id) WHERE archived = false;

-- ============================================================
-- Decisions + Actions (semantic memory)
-- ============================================================
CREATE TABLE IF NOT EXISTS decisions (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  topic        TEXT NOT NULL,
  body         TEXT,
  status       TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','made','reverted')),
  conv_id      UUID REFERENCES conversations(id) ON DELETE SET NULL,
  source_artifact_id UUID REFERENCES artifacts(id) ON DELETE SET NULL,
  made_at      TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS decisions_tenant_idx ON decisions(tenant_id, status, made_at DESC);

CREATE TABLE IF NOT EXISTS actions (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  decision_id  UUID REFERENCES decisions(id) ON DELETE CASCADE,
  owner_id     UUID REFERENCES members(id) ON DELETE SET NULL,
  description  TEXT NOT NULL,
  due_date     DATE,
  status       TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','in_progress','done','dropped')),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS actions_tenant_owner_idx ON actions(tenant_id, owner_id, status);

-- ============================================================
-- Events (raw signals from Discord/cron/web)
-- ============================================================
CREATE TABLE IF NOT EXISTS events (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  source       TEXT NOT NULL CHECK (source IN ('discord','github','cron','web','email')),
  kind         TEXT NOT NULL,
  member_id    UUID REFERENCES members(id) ON DELETE SET NULL,
  payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
  ts           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS events_tenant_ts_idx ON events(tenant_id, ts DESC);
CREATE INDEX IF NOT EXISTS events_tenant_source_kind_idx ON events(tenant_id, source, kind);

-- ============================================================
-- Usage / Billing
-- ============================================================
CREATE TABLE IF NOT EXISTS usage_events (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  member_id    UUID REFERENCES members(id) ON DELETE SET NULL,
  kind         TEXT NOT NULL,        -- 'query','ingest','embed'
  tokens_in    INT NOT NULL DEFAULT 0,
  tokens_out   INT NOT NULL DEFAULT 0,
  model        TEXT,
  cost_cents   INT NOT NULL DEFAULT 0,
  ts           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS usage_tenant_ts_idx ON usage_events(tenant_id, ts DESC);

CREATE TABLE IF NOT EXISTS usage_daily (
  tenant_id     UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  date          DATE NOT NULL,
  query_count   INT NOT NULL DEFAULT 0,
  tokens_total  BIGINT NOT NULL DEFAULT 0,
  cost_cents    BIGINT NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id, date)
);

-- ============================================================
-- Audit log (admin actions)
-- ============================================================
CREATE TABLE IF NOT EXISTS audit_log (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id    UUID REFERENCES tenants(id) ON DELETE SET NULL,
  actor_id     UUID REFERENCES members(id) ON DELETE SET NULL,
  action       TEXT NOT NULL,
  resource     TEXT,
  payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
  ts           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_log_ts_idx ON audit_log(ts DESC);

-- ============================================================
-- Row-Level Security policies
-- ============================================================
-- Strategy:
--   - app role: subject to RLS, must SET LOCAL app.tenant_id before each query
--   - service role: BYPASSRLS (granted at role creation)
-- Helper function: current tenant from session var
CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS UUID
LANGUAGE plpgsql STABLE AS $$
BEGIN
  RETURN nullif(current_setting('app.tenant_id', true), '')::UUID;
EXCEPTION WHEN others THEN
  RETURN NULL;
END;
$$;

-- Enable RLS on all tenant-scoped tables
ALTER TABLE tenants        ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscriptions  ENABLE ROW LEVEL SECURITY;
ALTER TABLE integrations   ENABLE ROW LEVEL SECURITY;
ALTER TABLE members        ENABLE ROW LEVEL SECURITY;
ALTER TABLE artifacts      ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks         ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations  ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages       ENABLE ROW LEVEL SECURITY;
ALTER TABLE decisions      ENABLE ROW LEVEL SECURITY;
ALTER TABLE actions        ENABLE ROW LEVEL SECURITY;
ALTER TABLE events         ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_events   ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_daily    ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log      ENABLE ROW LEVEL SECURITY;

-- Tenants: a session can only see/update its own row (admin via service role)
CREATE POLICY tenant_self ON tenants
  USING (id = current_tenant_id());

-- Generic tenant_id isolation policy (applies to every other table)
DO $$
DECLARE t TEXT;
BEGIN
  FOR t IN
    SELECT unnest(ARRAY['subscriptions','integrations','members','artifacts','chunks',
                        'conversations','messages','decisions','actions','events',
                        'usage_events','usage_daily','audit_log'])
  LOOP
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id = current_tenant_id());', t);
  END LOOP;
END $$;

-- Force RLS even for table owners (defensive)
ALTER TABLE tenants        FORCE ROW LEVEL SECURITY;
ALTER TABLE subscriptions  FORCE ROW LEVEL SECURITY;
ALTER TABLE integrations   FORCE ROW LEVEL SECURITY;
ALTER TABLE members        FORCE ROW LEVEL SECURITY;
ALTER TABLE artifacts      FORCE ROW LEVEL SECURITY;
ALTER TABLE chunks         FORCE ROW LEVEL SECURITY;
ALTER TABLE conversations  FORCE ROW LEVEL SECURITY;
ALTER TABLE messages       FORCE ROW LEVEL SECURITY;
ALTER TABLE decisions      FORCE ROW LEVEL SECURITY;
ALTER TABLE actions        FORCE ROW LEVEL SECURITY;
ALTER TABLE events         FORCE ROW LEVEL SECURITY;
ALTER TABLE usage_events   FORCE ROW LEVEL SECURITY;
ALTER TABLE usage_daily    FORCE ROW LEVEL SECURITY;
ALTER TABLE audit_log      FORCE ROW LEVEL SECURITY;

-- Note: curator_service role has BYPASSRLS — used for cron, ingest, admin ops
GRANT ALL ON ALL TABLES IN SCHEMA public TO curator_app, curator_service;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO curator_app, curator_service;

-- ============================================================
-- Done
-- ============================================================
INSERT INTO audit_log (action, payload) VALUES ('schema.init', jsonb_build_object('version','0.1','at',now()));
