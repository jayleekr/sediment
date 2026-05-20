#!/bin/sh
# Reusable admin-command wrapper for `fly ssh console -C` and the like.
#
# The Fly machine has the DATABASE_URL secret but only start.sh derives
# DATABASE_URL_APP/SERVICE + normalizes scheme/ssl. SSH sessions and
# release-command machines don't run start.sh, so anything that imports
# lab_lib.settings falls back to the localhost:5433 dev default and
# ConnectionRefuses. This wrapper mirrors start.sh:normalize_pg_url and
# then execs whatever you pass in.
#
# Usage:  fly ssh console -C "/run-with-db.sh python -m scripts.distill --since-hours 24"
set -e
: "${DATABASE_URL:?DATABASE_URL must be set}"

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
exec "$@"
