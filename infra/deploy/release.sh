#!/bin/sh
# fly.toml [deploy] release_command entrypoint.
#
# The release machine has the image + Fly secrets but does NOT run start.sh.
# start.sh is what derives DATABASE_URL_APP / DATABASE_URL_SERVICE from the
# DATABASE_URL secret AND normalizes scheme + ssl param. Without that, settings
# falls back to its localhost:5433 dev default → ConnectionRefused. So this
# mirrors start.sh's normalize_pg_url EXACTLY, then runs the idempotent seed
# (ALTER ... ADD COLUMN IF NOT EXISTS github_login + tenant/member upsert).
set -e
: "${DATABASE_URL:?DATABASE_URL must be set}"

# --- keep in sync with infra/deploy/start.sh:normalize_pg_url ---
normalize_pg_url() {
    u="$1"
    case "$u" in
        postgresql+asyncpg://*) : ;;
        postgresql://*)         u="postgresql+asyncpg://${u#postgresql://}" ;;
        postgres://*)           u="postgresql+asyncpg://${u#postgres://}" ;;
    esac
    case "$u" in
        *\?*sslmode=*|*\&sslmode=*) u="$(printf '%s' "$u" | sed 's/sslmode=/ssl=/')" ;;
    esac
    printf '%s' "$u"
}

export DATABASE_URL="$(normalize_pg_url "$DATABASE_URL")"
export DATABASE_URL_APP="$(normalize_pg_url "${DATABASE_URL_APP:-$DATABASE_URL}")"
export DATABASE_URL_SERVICE="$(normalize_pg_url "${DATABASE_URL_SERVICE:-$DATABASE_URL}")"

cd /app/services/sediment
echo "→ release: idempotent seed (github_login migrate + tenant/member upsert)"
exec python -m scripts.seed_lab
