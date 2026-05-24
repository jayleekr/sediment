# AI Curator — multi-service production image
#
# Packs all 4 FastAPI services + nginx into one container so Fly.io can run
# the whole stack on a single VM (cheap Phase 1 deployment).
#
#   Public surface (via nginx 8080):
#     /api/v1/*       → platform   :10100
#     /v1/sediment/*   → langgraph  :10020
#     /webhook/*      → ingester   :11000
#     /healthz        → platform   :10100
#
#   Internal-only (not exposed):
#     metadata-svc :12000  (used by validator checks)

FROM python:3.11-slim AS builder

# Build deps for asyncpg, cryptography, etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY services/sediment/pyproject.toml services/sediment/
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e services/sediment/

# Copy the actual application code after deps so dep changes don't bust the
# layer cache on every code edit.
COPY services/sediment/ services/sediment/


FROM python:3.11-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx supervisor libpq5 curl gettext-base \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app /app

# Process manager + nginx routing + entrypoint.
# 2026-05-23 FIX-B: nginx.conf becomes a template — start.sh envsubst's
# ${ANTHROPIC_PROXY_SECRET} into it at container boot before launching nginx
# (closes REPORT.md CRIT #3 — open Anthropic relay). gettext-base above
# provides envsubst.
COPY infra/deploy/supervisord.conf /etc/supervisor/conf.d/sediment.conf
COPY infra/deploy/nginx.conf       /etc/nginx/nginx.conf.tmpl
COPY infra/deploy/start.sh         /start.sh
COPY infra/deploy/release.sh       /release.sh
COPY infra/deploy/run-with-db.sh   /run-with-db.sh

# Ship the migration files so release.sh can apply them. Lives at
# /app/infra/migrations/ to match the path apply_migrations.py expects
# (Path(__file__).resolve().parents[3] = /app from
# /app/services/sediment/scripts/apply_migrations.py).
COPY infra/migrations/            /app/infra/migrations/
RUN chmod +x /start.sh /release.sh /run-with-db.sh

# Default ports (override via Fly env). Public port is 8080 (nginx).
ENV CURATOR_PLATFORM_PORT=10100 \
    CURATOR_LANGGRAPH_PORT=10020 \
    VAULT_INGESTER_PORT=11000 \
    METADATA_SVC_PORT=12000 \
    PUBLIC_PORT=8080

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8080/healthz || exit 1

CMD ["/start.sh"]
