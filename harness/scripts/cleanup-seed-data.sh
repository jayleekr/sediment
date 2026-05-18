#!/usr/bin/env bash
# Purge validator seed data from the DB (artifacts + conversations).
# Safe to run any time; idempotent. Pass --dry-run to preview row counts.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
ENV_FILE="$REPO_ROOT/products/sediment/.env"

DRY_RUN=0
for arg in "$@"; do [[ "$arg" == "--dry-run" ]] && DRY_RUN=1; done

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERR: .env not found at $ENV_FILE" >&2; exit 1
fi
source "$ENV_FILE" 2>/dev/null || true

# Resolve DATABASE_URL from env (supports postgres:// and postgresql://)
DB_URL="${DATABASE_URL:-}"
if [[ -z "$DB_URL" ]]; then
  echo "ERR: DATABASE_URL not set in .env" >&2; exit 1
fi

if [[ $DRY_RUN -eq 1 ]]; then
  echo "=== dry-run: counting seed rows ==="
  psql "$DB_URL" -c "SELECT COUNT(*) AS artifact_seed_rows FROM artifacts WHERE ref ~ '^validator/(idem|sample)-';"
  psql "$DB_URL" -c "SELECT COUNT(*) AS conv_seed_rows FROM conversations WHERE title IN ('sec-check','lab-priv','rls-check');"
  exit 0
fi

echo "Deleting validator/idem-* and validator/sample-* artifacts…"
psql "$DB_URL" -c "DELETE FROM artifacts WHERE ref ~ '^validator/(idem|sample)-';"

echo "Deleting seed conversations (sec-check, lab-priv, rls-check)…"
psql "$DB_URL" -c "DELETE FROM conversations WHERE title IN ('sec-check','lab-priv','rls-check');"

echo "Done."
