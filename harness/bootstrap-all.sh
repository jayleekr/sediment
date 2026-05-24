#!/usr/bin/env bash
# One-shot bootstrap: infra → deps → seed → services → first validation → Ralph.
# Idempotent — safe to run multiple times.
#
# Run from repo root:
#   ./harness/bootstrap-all.sh [--with-ralph]
#
# Stages (each logged + skippable if already done):
#   STAGE 1  docker up (Postgres + Redis)
#   STAGE 2  python venv + pip install
#   STAGE 3  playwright chromium install
#   STAGE 4  seed default tenant + 8 members
#   STAGE 5  start vault-ingester :11000 (background)
#   STAGE 6  initial ingest (corpus build)
#   STAGE 7  start metadata-svc :12000, platform :10100, langgraph :10020 (background)
#   STAGE 8  validate P0 (single shot)
#   STAGE 9  (--with-ralph) start Ralph loop (background)
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$REPO_ROOT/output/bootstrap"
mkdir -p "$LOG_DIR"
ENV="$REPO_ROOT/.env"

# PATH augmentation — Docker Desktop credential helpers + Homebrew (macOS).
# Without this, docker compose pull fails with "docker-credential-desktop not found".
export PATH="/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH"

WITH_RALPH=0
[ "${1:-}" = "--with-ralph" ] && WITH_RALPH=1

stage() { echo; echo "═══ STAGE $1 ── $2"; date; }
ok()    { echo "  ✓ $1"; }
warn()  { echo "  ⚠ $1"; }
fail()  { echo "  ✗ $1"; }

# ────────────────────────────────────────────────────────────
stage 1 "docker up"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q curator-pg; then
  ok "postgres already running"
else
  cd "$REPO_ROOT"
  docker compose -f infra/docker-compose.yml up -d 2>&1 | tee "$LOG_DIR/01-docker.log"
fi
# wait healthy
for i in {1..30}; do
  if docker exec curator-pg pg_isready -U curator -d curator >/dev/null 2>&1; then
    ok "postgres healthy"; break
  fi
  sleep 1
done

# ────────────────────────────────────────────────────────────
stage 2 "python venv + deps"
SVC="$REPO_ROOT/services/sediment"
if [ -x "$SVC/.venv/bin/python" ]; then
  ok "venv exists"
else
  cd "$SVC"
  python3 -m venv .venv
  ./.venv/bin/pip install --upgrade pip > "$LOG_DIR/02-pip-upgrade.log" 2>&1
  ./.venv/bin/pip install -e ".[dev]" > "$LOG_DIR/02-pip.log" 2>&1
  ok "deps installed"
fi

# ────────────────────────────────────────────────────────────
stage 3 "playwright chromium"
if [ -d "$HOME/Library/Caches/ms-playwright/chromium" ] || \
   [ -d "$HOME/.cache/ms-playwright/chromium" ]; then
  ok "chromium already installed"
else
  "$SVC/.venv/bin/playwright" install chromium > "$LOG_DIR/03-playwright.log" 2>&1 || \
    warn "playwright install failed (E2E checks will be skipped)"
fi

# ────────────────────────────────────────────────────────────
stage 4 "seed"
if [ ! -f "$ENV" ]; then
  cp "$REPO_ROOT/.env.example" "$ENV"
  warn "created $ENV from example — edit it to add API keys, then re-run"
fi
cd "$SVC"
.venv/bin/python -m scripts.seed_lab > "$LOG_DIR/04-seed.log" 2>&1 && ok "seeded" || \
  fail "seed failed (see 04-seed.log)"

# ────────────────────────────────────────────────────────────
stage 5 "vault-ingester :11000"
if curl -fsS http://localhost:11000/healthz >/dev/null 2>&1; then
  ok "ingester already up"
else
  cd "$SVC"
  nohup .venv/bin/uvicorn applications.vault_ingester.main:app \
    --port 11000 > "$LOG_DIR/05-ingester.log" 2>&1 &
  echo $! > "$LOG_DIR/ingester.pid"
  for i in {1..15}; do
    sleep 1
    curl -fsS http://localhost:11000/healthz >/dev/null 2>&1 && { ok "ingester up"; break; }
  done
fi

# ────────────────────────────────────────────────────────────
stage 6 "initial ingest"
INGEST_MARK="$SVC/.first-ingest-done"
if [ -f "$INGEST_MARK" ]; then
  ok "already ingested"
else
  cd "$SVC"
  .venv/bin/python -m scripts.ingest_repo > "$LOG_DIR/06-ingest.log" 2>&1 && {
    touch "$INGEST_MARK"; ok "ingested";
  } || warn "ingest had errors (see 06-ingest.log) — continuing"
fi

# ────────────────────────────────────────────────────────────
stage 7 "platform :10100 + langgraph :10020 + metadata :12000"
# 2026-05-23 fix: module names renamed curator_* → sediment_* months ago,
# bootstrap-all.sh wasn't updated → ModuleNotFoundError on every fresh-clone.
for svc_port in "metadata_svc:12000" "sediment_platform:10100" "sediment_langgraph:10020"; do
  app="${svc_port%%:*}"; port="${svc_port##*:}"
  if curl -fsS "http://localhost:$port/healthz" >/dev/null 2>&1; then
    ok "$app already up"
    continue
  fi
  cd "$SVC"
  nohup .venv/bin/uvicorn "applications.$app.main:app" --port "$port" \
    > "$LOG_DIR/07-$app.log" 2>&1 &
  echo $! > "$LOG_DIR/$app.pid"
  for i in {1..15}; do
    sleep 1
    curl -fsS "http://localhost:$port/healthz" >/dev/null 2>&1 && { ok "$app up on :$port"; break; }
  done
done

# ────────────────────────────────────────────────────────────
stage 8 "validate P0"
cd "$SVC"
.venv/bin/python -m validator --phase P0 > "$LOG_DIR/08-validate-p0.log" 2>&1
rc=$?
case $rc in
  0) ok "P0 passed" ;;
  2) warn "P0 score < 90% (see 08-validate-p0.log)" ;;
  *) fail "P0 blocker fail (see 08-validate-p0.log) — investigate" ;;
esac

# ────────────────────────────────────────────────────────────
if [ "$WITH_RALPH" = "1" ]; then
  stage 9 "Ralph loop (background, with supervisor)"
  cd "$REPO_ROOT"
  nohup bash harness/ralph/supervisor.sh \
    --max-restarts 15 --cooldown 120 \
    > "$LOG_DIR/09-ralph.log" 2>&1 &
  echo $! > "$LOG_DIR/ralph.pid"
  ok "Ralph (under supervisor) started, pid=$(cat $LOG_DIR/ralph.pid)"
fi

echo
echo "═══ Bootstrap complete ═══"
echo "  logs:           $LOG_DIR"
echo "  validation:     $REPO_ROOT/output/validation/P0-latest.md"
echo "  monitor live:   bash harness/monitor/watch.sh"
[ "$WITH_RALPH" = "1" ] && echo "  ralph journal:  $REPO_ROOT/harness/ralph/JOURNAL.md"
