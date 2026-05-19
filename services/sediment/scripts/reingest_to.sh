#!/usr/bin/env bash
# Re-ingest the full vault into the DB pointed at by $DATABASE_URL.
# Used for the Supabase/pgvector migration (infra/SUPABASE_MIGRATION.md step 4)
# and any "rebuild the index" need.
#
#   DATABASE_URL=postgres://...supabase...?ssl=require \
#     bash services/sediment/scripts/reingest_to.sh /path/to/hypeprooflab
#
# Arg 1 = checkout that holds the VAULT CONTENT (hypeprooflab — research/,
#         web/src/content/, novels/, …). NOT this product repo.
# Requires: $DATABASE_URL, $OPENAI_API_KEY (embeddings), the service .venv.
set -euo pipefail

VAULT_ROOT="${1:?usage: reingest_to.sh /path/to/hypeprooflab  (vault content checkout)}"
: "${DATABASE_URL:?set DATABASE_URL to the TARGET db (e.g. Supabase)}"

SVC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # services/sediment
PY="$SVC_DIR/.venv/bin/python"
[ -x "$PY" ] || { echo "missing $PY — run 'make install' first"; exit 1; }
[ -d "$VAULT_ROOT/research" ] || [ -d "$VAULT_ROOT/web/src/content" ] || {
  echo "‼ $VAULT_ROOT has no research/ or web/src/content/ — wrong checkout?"; exit 1; }

export DATABASE_URL SEDIMENT_VAULT_ROOT="$VAULT_ROOT"

echo "→ starting local vault_ingester against the target DB…"
( cd "$SVC_DIR" && "$PY" -m uvicorn applications.vault_ingester.main:app \
    --port "${VAULT_INGESTER_PORT:-11000}" --log-level warning ) &
ING_PID=$!
trap 'kill "$ING_PID" 2>/dev/null || true' EXIT

# wait for /healthz
for i in $(seq 1 30); do
  curl -fsS "http://localhost:${VAULT_INGESTER_PORT:-11000}/healthz" >/dev/null 2>&1 && break
  sleep 1
  [ "$i" = 30 ] && { echo "ingester did not come up"; exit 1; }
done

echo "→ ingesting vault from $VAULT_ROOT …"
( cd "$SVC_DIR" && "$PY" -m scripts.ingest_repo )

echo "✓ re-ingest done. Now run the recall check:"
echo "  $PY -m validator.checks.regression_rag"
