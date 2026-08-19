"""Forward-only SQL migration runner.

Applies every `infra/migrations/NNN_*.sql` that hasn't been recorded in
`schema_migrations` yet, in lexicographic order.

Usage:
  python -m scripts.apply_migrations               # uses settings.database_url
  python -m scripts.apply_migrations --dry-run     # show what would run
  python -m scripts.apply_migrations --target 003  # apply through 003 only
"""
from __future__ import annotations
import argparse
import asyncio
import re
import sys
from pathlib import Path

import asyncpg

from lab_lib.settings import settings


MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "infra" / "migrations"
# `.rollback.sql` companions are NOT migrations. infra/migrations/README.md
# mandates a paired rollback file for any migration that DROPs or replaces a
# constraint, and `005_x.rollback.sql` matches the plain `NNN_*.sql` shape — so
# without this exclusion the runner would happily apply the rollback right
# after the migration it undoes. No such pair existed before 005 (sediment#140),
# which is why the convention and the runner had never met.
NAME_RE = re.compile(r"^(\d{3})_(?!.*\.rollback\.sql$).+\.sql$")

ROLLBACK_SUFFIX = ".rollback.sql"


def discover() -> list[Path]:
    files = sorted(
        p for p in MIGRATIONS_DIR.glob("*.sql")
        if not p.name.endswith(ROLLBACK_SUFFIX) and NAME_RE.match(p.name)
    )
    return files


def stem(p: Path) -> str:
    return p.stem  # e.g. "001_cli_multiuser"


async def applied_set(conn: asyncpg.Connection) -> set[str]:
    # Lazy-create bookkeeping table so the very first run works.
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
          name        TEXT PRIMARY KEY,
          applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    rows = await conn.fetch("SELECT name FROM schema_migrations")
    return {r["name"] for r in rows}


async def run(dry_run: bool, target: str | None) -> int:
    files = discover()
    if not files:
        print("no migrations found", file=sys.stderr)
        return 0

    # Migrations need DDL privileges (CREATE TABLE, ALTER, GRANT). Neither
    # curator_app nor curator_service has those — only the superuser does.
    # Use SEDIMENT_MIGRATIONS_DB_URL when set (CI / prod), otherwise derive
    # from the service URL by swapping in the local superuser creds.
    import os
    raw = os.environ.get("SEDIMENT_MIGRATIONS_DB_URL")
    if not raw:
        svc = settings.database_url_service.replace("+asyncpg", "")
        # postgresql://curator_service:curator_service_local@localhost:5433/curator
        # → postgresql://curator:curator_local_dev@localhost:5433/curator
        raw = svc.replace(
            "curator_service:curator_service_local",
            "curator:curator_local_dev",
        )
    url = raw
    conn = await asyncpg.connect(url)
    try:
        already = await applied_set(conn)
        pending = [f for f in files if stem(f) not in already]
        if target:
            pending = [f for f in pending if stem(f).split("_", 1)[0] <= target]
        if not pending:
            print("up to date")
            return 0
        for f in pending:
            print(f"==> {f.name}{' (dry-run)' if dry_run else ''}")
            sql = f.read_text()
            if dry_run:
                continue
            # Each migration file owns its own BEGIN/COMMIT — execute as one
            # script via asyncpg's `execute` (handles multi-statement).
            await conn.execute(sql)
        return 0
    finally:
        await conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--target", help="apply through this NNN prefix only")
    args = ap.parse_args()
    return asyncio.run(run(args.dry_run, args.target))


if __name__ == "__main__":
    raise SystemExit(main())
