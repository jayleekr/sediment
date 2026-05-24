#!/usr/bin/env bash
# Idempotent environment setup. Safe to re-run anytime.
# Implements 8 stages from /curator:setup skill.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SVC="$REPO_ROOT/services/sediment"
ENV_FILE="$REPO_ROOT/.env"
LOG_DIR="$REPO_ROOT/output/setup"
mkdir -p "$LOG_DIR"

LOG="$LOG_DIR/setup-$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$LOG") 2>&1

# PATH augmentation always-on
export PATH="/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH"

ok()   { echo "  ✓ $1"; }
warn() { echo "  ⚠ $1"; }
miss() { echo "  ○ $1 — fixing"; }
fail() { echo "  ✗ $1"; }

stage() { echo; echo "── stage $1 ── $2"; }

# ────────── 1. Docker daemon ──────────
stage 1 "docker daemon"
if docker info >/dev/null 2>&1; then
  ok "daemon reachable"
else
  warn "Docker daemon not running. Trying open -a Docker..."
  open -a Docker 2>/dev/null || true
  for i in $(seq 1 30); do
    sleep 2
    docker info >/dev/null 2>&1 && { ok "daemon now up"; break; }
  done
  docker info >/dev/null 2>&1 || { fail "still down after 60s — start Docker Desktop manually"; exit 1; }
fi

# ────────── 2. docker credential helper ──────────
stage 2 "docker credential helper"
if command -v docker-credential-desktop >/dev/null 2>&1; then
  ok "docker-credential-desktop in PATH"
else
  miss "docker-credential-desktop not in PATH (PATH already augmented above; if still missing, helper may be uninstalled)"
fi

# ────────── 3. Python 3.11+ ──────────
stage 3 "Python 3.11+"
PY311=""
for cand in python3.13 python3.12 python3.11 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.11; do
  if command -v "$cand" >/dev/null 2>&1; then
    v=$("$cand" -c 'import sys; print(sys.version_info[:2])' 2>/dev/null)
    case "$v" in
      "(3, 11)"|"(3, 12)"|"(3, 13)"|"(3, 14)") PY311="$cand"; break ;;
    esac
  fi
done
if [ -n "$PY311" ]; then
  ok "found $PY311 ($($PY311 --version))"
else
  fail "no Python 3.11+ found. brew install python@3.11"
  exit 1
fi

# ────────── 4. venv ──────────
stage 4 "venv at $SVC/.venv"
if [ -x "$SVC/.venv/bin/python" ]; then
  cur_v=$("$SVC/.venv/bin/python" -c 'import sys; print(sys.version_info[:2])')
  if [[ "$cur_v" == "(3, 11)" || "$cur_v" == "(3, 12)" || "$cur_v" == "(3, 13)" || "$cur_v" == "(3, 14)" ]]; then
    ok "venv Python $cur_v"
  else
    miss "venv has wrong Python ($cur_v) — recreating with $PY311"
    rm -rf "$SVC/.venv"
    "$PY311" -m venv "$SVC/.venv"
    "$SVC/.venv/bin/pip" install --upgrade pip >/dev/null
  fi
else
  miss "venv missing — creating"
  "$PY311" -m venv "$SVC/.venv"
  "$SVC/.venv/bin/pip" install --upgrade pip >/dev/null
fi

# ────────── 5. core deps ──────────
stage 5 "core deps"

# Ensure pip is in the venv (some pre-existing venvs lack pip)
if ! "$SVC/.venv/bin/python" -m pip --version >/dev/null 2>&1; then
  miss "pip missing in venv — bootstrapping via ensurepip"
  "$SVC/.venv/bin/python" -m ensurepip --upgrade >/dev/null 2>&1
  "$SVC/.venv/bin/python" -m pip install --upgrade pip >/dev/null 2>&1
fi

need_install=0
"$SVC/.venv/bin/python" -c "import fastapi, sqlalchemy, asyncpg, pgvector, langgraph, anthropic, openai, yaml, frontmatter, structlog, jose, tiktoken, fastmcp" 2>/dev/null \
  && ok "all core deps importable" \
  || { miss "some deps missing — running pip install -e .[dev]"; need_install=1; }

if [ "$need_install" = "1" ]; then
  cd "$SVC"
  .venv/bin/python -m pip install -e ".[dev]" 2>&1 | tail -5
  cd "$REPO_ROOT"
  # Re-verify after install
  "$SVC/.venv/bin/python" -c "import fastapi, sqlalchemy, asyncpg, langgraph" 2>/dev/null \
    && ok "deps now importable" \
    || fail "deps still not importable after install — see pip log"
fi

# ────────── 6. playwright chromium ──────────
stage 6 "playwright chromium"
chromium_ok=0
for d in "$HOME/Library/Caches/ms-playwright/chromium" \
         "$HOME/Library/Caches/ms-playwright/chromium-"* \
         "$HOME/.cache/ms-playwright/chromium" \
         "$HOME/.cache/ms-playwright/chromium-"*; do
  [ -d "$d" ] && chromium_ok=1
done
if [ "$chromium_ok" = "1" ]; then
  ok "chromium cached"
else
  miss "chromium not installed — running playwright install (large download)"
  "$SVC/.venv/bin/playwright" install chromium 2>&1 | tail -3
fi

# ────────── 7. .env ──────────
stage 7 ".env"
if [ ! -f "$ENV_FILE" ]; then
  miss ".env missing — copying from .env.example"
  cp "$REPO_ROOT/.env.example" "$ENV_FILE"
fi
if grep -q '^ANTHROPIC_API_KEY=sk-ant-\.\.\.' "$ENV_FILE" || \
   grep -q '^ANTHROPIC_API_KEY=$' "$ENV_FILE" 2>/dev/null; then
  warn "ANTHROPIC_API_KEY is placeholder — Phase 2 SSE will fail without real key"
else
  ok "ANTHROPIC_API_KEY appears set"
fi
if grep -q '^OPENAI_API_KEY=sk-\.\.\.' "$ENV_FILE" || \
   grep -q '^OPENAI_API_KEY=$' "$ENV_FILE" 2>/dev/null; then
  warn "OPENAI_API_KEY is placeholder — embeddings will be zero-vectors (RAG quality degraded)"
else
  ok "OPENAI_API_KEY appears set"
fi

# ────────── 8. infra containers ──────────
stage 8 "infra containers"
running=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -E '^curator-(pg|redis)$' | wc -l)
if [ "$running" -ge 2 ]; then
  ok "postgres + redis up"
else
  miss "starting docker compose"
  docker compose -f "$REPO_ROOT/infra/docker-compose.yml" up -d 2>&1 | tail -5
  for i in $(seq 1 30); do
    sleep 2
    docker exec curator-pg pg_isready -U curator -d curator >/dev/null 2>&1 && { ok "postgres ready"; break; }
  done
fi

echo
echo "═══ setup complete (log: $LOG) ═══"
