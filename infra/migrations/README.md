# Schema migrations

Forward-only, idempotent SQL files applied on top of `init.sql` for running
clusters (local Docker, Fly, Supabase) that cannot be re-bootstrapped.

## Naming

`NNN_short_topic.sql` where `NNN` is zero-padded sequential.

## Rules

- Every statement uses `IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS` so the
  file is safe to re-apply.
- No `DROP` / `ALTER COLUMN TYPE` without a paired rollback file
  (`NNN_short_topic.rollback.sql`).
- RLS policies on new tenant-scoped tables MUST be added in the same file
  as the table.
- Each file ends by inserting its name into `schema_migrations(name)`.

## Apply

```bash
make migrate           # local Docker Postgres
make migrate-prod      # gated; runs against $DATABASE_URL
```

The `schema_migrations` table is itself created lazily by the migrate
script. See `scripts/apply_migrations.py`.
