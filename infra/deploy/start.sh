#!/bin/sh
# Container entrypoint. Fly sets the secrets; we sanity-check the must-haves
# and hand off to supervisord.

set -e

: "${DATABASE_URL:?DATABASE_URL must be set (Fly Postgres URL or external)}"
: "${JWT_SECRET:?JWT_SECRET must be set (used by lab_lib.auth)}"

# Optional but warned — log a clear message instead of silent failure.
[ -z "$ANTHROPIC_API_KEY" ] && echo "WARN: ANTHROPIC_API_KEY unset — langgraph will return offline-mock answers"
[ -z "$GITHUB_WEBHOOK_SECRET" ] && echo "WARN: GITHUB_WEBHOOK_SECRET unset — webhook endpoint will skip signature check"

# SQLAlchemy needs `postgresql+asyncpg://` (not `postgres+asyncpg://`).
# Fly Postgres sets DATABASE_URL=postgres://...?sslmode=disable; normalize both
# the scheme AND the SSL param:
#   - asyncpg rejects libpq's `?sslmode=disable` (TypeError: unexpected kwarg)
#   - dropping it entirely makes asyncpg DEFAULT to SSL → ConnectionReset on
#     Fly's non-TLS flycast hop. asyncpg's own param is `?ssl=disable`.
# So: sslmode=<x> → ssl=<x>  (disable/prefer/require/allow all map 1:1 enough
# for our use; flycast is always `disable`).
normalize_pg_url() {
    u="$1"
    case "$u" in
        postgresql+asyncpg://*) : ;;
        postgresql://*)         u="postgresql+asyncpg://${u#postgresql://}" ;;
        postgres://*)           u="postgresql+asyncpg://${u#postgres://}" ;;
    esac
    # libpq sslmode → asyncpg ssl
    case "$u" in
        *\?*sslmode=*|*\&sslmode=*) u="$(printf '%s' "$u" | sed 's/sslmode=/ssl=/')" ;;
    esac
    printf '%s' "$u"
}
export DATABASE_URL="$(normalize_pg_url "$DATABASE_URL")"
# Per-role URLs (RLS). Default to DATABASE_URL if not set so single-DB deploys
# (e.g. Fly Postgres with one user) still work.
if [ -z "${DATABASE_URL_APP:-}" ]; then
    export DATABASE_URL_APP="$DATABASE_URL"
fi
if [ -z "${DATABASE_URL_SERVICE:-}" ]; then
    export DATABASE_URL_SERVICE="$DATABASE_URL"
fi
export DATABASE_URL_APP="$(normalize_pg_url "$DATABASE_URL_APP")"
export DATABASE_URL_SERVICE="$(normalize_pg_url "$DATABASE_URL_SERVICE")"

echo "→ Starting AI Curator container"
echo "   PUBLIC_PORT=$PUBLIC_PORT"
echo "   platform=$SEDIMENT_PLATFORM_PORT langgraph=$SEDIMENT_LANGGRAPH_PORT"
echo "   ingester=$VAULT_INGESTER_PORT metadata=$METADATA_SVC_PORT"

exec /usr/bin/supervisord -c /etc/supervisor/conf.d/sediment.conf
