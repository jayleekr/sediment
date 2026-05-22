-- Supabase role bootstrap for Sediment prod.
--
-- WHY THIS FILE EXISTS
--
-- infra/init.sql creates `curator_app` / `curator_service` for the local
-- dockerized Postgres. Prod uses Supabase, where:
--   - we don't own the cluster (can't run init.sql wholesale)
--   - the default role is `postgres` (Supabase superuser)
--   - we need NON-superuser roles to be the DATABASE_URL_APP/SERVICE
--     targets so RLS actually applies (postgres BYPASSRLS, defeats RLS)
--
-- Earlier (before 2026-05-21) prod used the `postgres` role directly for
-- both URLs — that was the cross-tenant leak in sediment#16. After the
-- fix, prod uses `sediment_app` (NOT BYPASSRLS) and `sediment_service`
-- (BYPASSRLS for trusted server-side ops only).
--
-- This file is IDEMPOTENT. Re-running it on an already-bootstrapped
-- Supabase project is a no-op. Run as the `postgres` superuser via the
-- Supabase SQL editor or via psql with the Supabase pooler URL.
--
-- USAGE
--   1. Supabase Dashboard → SQL Editor → paste + run, OR
--   2. psql "$SUPABASE_POOLER_URL_AS_POSTGRES" -f infra/supabase-bootstrap.sql
--
-- Roles created:
--   sediment_app      — NOT BYPASSRLS. The user-facing API role. Per-request
--                       SET app.tenant_id ensures RLS filters apply.
--   sediment_service  — BYPASSRLS. Server-side service role for cron jobs,
--                       background workers, RLS-aware ingest. Must explicitly
--                       include `WHERE tenant_id = :tid` in every query
--                       (defense-in-depth — see sediment#16).
--
-- Passwords: set inline below for the first bootstrap, OR run the
-- `ALTER ROLE ... PASSWORD '...'` lines from a separate shell to keep
-- the password out of git. Use a 24+ char random password.

-- ----------------------------------------------------------------------
-- Role creation (idempotent)
-- ----------------------------------------------------------------------

DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sediment_app') THEN
        CREATE ROLE sediment_app NOINHERIT LOGIN
            PASSWORD 'CHANGE_ME_BEFORE_RUNNING_THIS_FILE';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sediment_service') THEN
        CREATE ROLE sediment_service NOINHERIT LOGIN BYPASSRLS
            PASSWORD 'CHANGE_ME_BEFORE_RUNNING_THIS_FILE';
    END IF;
END $$;

-- ----------------------------------------------------------------------
-- Schema + DML grants
-- ----------------------------------------------------------------------

GRANT CONNECT ON DATABASE postgres TO sediment_app, sediment_service;
GRANT USAGE  ON SCHEMA   public    TO sediment_app, sediment_service;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public
    TO sediment_app, sediment_service;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public
    TO sediment_app, sediment_service;

-- Future tables (created by ALTER TABLE in seed_lab.py etc.) get the same
-- grants automatically. Without this, new tables would be inaccessible to
-- the service roles until a manual GRANT.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES
    TO sediment_app, sediment_service;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES
    TO sediment_app, sediment_service;

-- pgvector access — required for similarity search SQL by both roles.
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO sediment_app, sediment_service;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT EXECUTE ON FUNCTIONS TO sediment_app, sediment_service;

-- ----------------------------------------------------------------------
-- After running this:
--   1. Set the passwords:
--        ALTER ROLE sediment_app     PASSWORD '<random-24-char>';
--        ALTER ROLE sediment_service PASSWORD '<random-24-char>';
--   2. Set the matching fly secrets:
--        fly secrets set \
--          DATABASE_URL_APP="postgresql+asyncpg://sediment_app:<pwd>@<pooler-host>:5432/postgres" \
--          DATABASE_URL_SERVICE="postgresql+asyncpg://sediment_service:<pwd>@<pooler-host>:5432/postgres" \
--          SEDIMENT_MIGRATIONS_DB_URL="postgresql://postgres:<superuser-pwd>@<pooler-host>:5432/postgres" \
--          -a hypeproof-sediment
--   3. The `postgres` superuser URL goes into SEDIMENT_MIGRATIONS_DB_URL
--      ONLY — never into DATABASE_URL_APP/SERVICE. It bypasses RLS.
-- ----------------------------------------------------------------------
