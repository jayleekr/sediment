#!/usr/bin/env bash
# preflight.sh — local stack readiness check.
#
# Bundles the "is this branch good to keep working on?" survey that we
# kept running by hand: docker up? venv ok? uvicorn ports answering?
# env vars complete? lint clean? validator-P0 green?
#
# Output is bucketed: PASS / WARN / FAIL. Exit codes:
#   0 — everything PASS (or WARN-only)
#   1 — at least one FAIL — fix before continuing
#
# Usage:
#   bash harness/scripts/preflight.sh            # full check
#   bash harness/scripts/preflight.sh --quick    # skip slow steps (validator, e2e)
#
# Designed to be idempotent + safe to run anytime.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SVC="$REPO_ROOT/services/sediment"

QUICK=0
for arg in "$@"; do
  case "$arg" in
    --quick|-q) QUICK=1 ;;
  esac
done

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
FAILURES=()

pass()  { printf "  \033[32m✓\033[0m %s\n" "$1"; PASS_COUNT=$((PASS_COUNT + 1)); }
warn()  { printf "  \033[33m⚠\033[0m %s\n" "$1"; WARN_COUNT=$((WARN_COUNT + 1)); }
fail()  { printf "  \033[31m✗\033[0m %s\n" "$1"; FAIL_COUNT=$((FAIL_COUNT + 1)); FAILURES+=("$1"); }
stage() { echo; printf "\033[1m── %s ──\033[0m\n" "$1"; }

# ──────────────────────────────────────────────────────────────────────
stage "1. docker — postgres + redis"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^curator-pg$'; then
  if docker exec curator-pg pg_isready -U curator -d curator >/dev/null 2>&1; then
    pass "postgres healthy (curator-pg)"
  else
    fail "postgres container up but pg_isready failed"
  fi
else
  fail "curator-pg not running — run: make up"
fi
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^curator-redis$'; then
  pass "redis up (curator-redis)"
else
  warn "redis not running — make up will start it"
fi

# ──────────────────────────────────────────────────────────────────────
stage "2. python venv + core deps"
if [ -x "$SVC/.venv/bin/python" ]; then
  v=$("$SVC/.venv/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)
  case "$v" in
    3.11|3.12|3.13|3.14) pass "venv python=$v" ;;
    *) fail "venv python=$v (need 3.11+) — make install" ;;
  esac
  if "$SVC/.venv/bin/python" -c "import fastapi, sqlalchemy, asyncpg, langgraph, anthropic, openai, yaml, jose, tiktoken, fastmcp, stripe" 2>/dev/null; then
    pass "core deps importable (incl. stripe)"
  else
    fail "missing deps — make install"
  fi
else
  fail "venv missing — make install"
fi

# ──────────────────────────────────────────────────────────────────────
stage "3. .env completeness"
ENV_FILE="$REPO_ROOT/.env"
EXAMPLE="$REPO_ROOT/.env.example"
if [ ! -f "$ENV_FILE" ]; then
  warn ".env missing — cp .env.example .env and fill keys"
else
  pass ".env present"
  # Variables that MUST exist in .env (declared in .env.example, no value
  # validation — just key presence).
  missing=()
  while IFS= read -r line; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" =~ ^[[:space:]]*$ ]] && continue
    key="${line%%=*}"
    [ -z "$key" ] && continue
    if ! grep -qE "^${key}=" "$ENV_FILE" 2>/dev/null; then
      missing+=("$key")
    fi
  done < "$EXAMPLE"
  if [ "${#missing[@]:-0}" -eq 0 ]; then
    pass ".env declares all keys from .env.example"
  else
    warn ".env missing keys vs example: ${missing[*]:0:8}$([ ${#missing[@]} -gt 8 ] && echo ' ...')"
  fi
fi

# ──────────────────────────────────────────────────────────────────────
stage "4. lint — :NAME::TYPE SQL cast guard"
if bash "$SCRIPT_DIR/lint-sql-cast.sh" >/dev/null 2>&1; then
  pass "lint-sql-cast clean"
else
  fail "lint-sql-cast violations — bash harness/scripts/lint-sql-cast.sh"
fi

# ──────────────────────────────────────────────────────────────────────
stage "5. syntax — every changed .py file parses"
# Cheap AST check on tracked .py files modified since last commit.
# Bash 3.2 compatible (mapfile is bash 4+). macOS still ships 3.2.
parse_fail=0
changed_count=0
while IFS= read -r f; do
  [ -z "$f" ] && continue
  changed_count=$((changed_count + 1))
  if ! "$SVC/.venv/bin/python" -c "import ast; ast.parse(open('$REPO_ROOT/$f').read())" 2>/dev/null; then
    fail "syntax error in $f"
    parse_fail=1
  fi
done < <(git -C "$REPO_ROOT" diff --name-only HEAD -- '*.py' 2>/dev/null)
[ "$parse_fail" = "0" ] && pass "$changed_count changed .py files parse cleanly"

# ──────────────────────────────────────────────────────────────────────
stage "6. uvicorn service ports (optional)"
# Bash 3.2 compatible — no associative arrays. Parallel pairs.
for pair in "10100:sediment_platform" "10020:sediment_langgraph" "11000:vault_ingester" "12000:metadata_svc"; do
  port="${pair%%:*}"
  name="${pair##*:}"
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 "http://localhost:$port/healthz" 2>/dev/null || echo "000")
  if [ "$code" = "200" ]; then
    pass "$name :$port healthy"
  else
    warn "$name :$port down (run: cd $SVC && .venv/bin/uvicorn applications.$name.main:app --port $port)"
  fi
done

# ──────────────────────────────────────────────────────────────────────
if [ "$QUICK" = "0" ]; then
  stage "7. validator P0 (slow — pass --quick to skip)"
  if cd "$REPO_ROOT" && make validate-p0 >/tmp/preflight-p0.log 2>&1; then
    # Pull the score line
    score=$(grep -E '^.+ weight' /tmp/preflight-p0.log | head -1 | tr -d ' ')
    pass "validate-p0 PASS ($score)"
  else
    fail "validate-p0 FAIL — see /tmp/preflight-p0.log (tail)"
    tail -8 /tmp/preflight-p0.log | sed 's/^/    /'
  fi
fi

# ──────────────────────────────────────────────────────────────────────
stage "Summary"
total=$((PASS_COUNT + WARN_COUNT + FAIL_COUNT))
printf "  pass: \033[32m%d\033[0m  warn: \033[33m%d\033[0m  fail: \033[31m%d\033[0m   (total %d)\n" \
  "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" "$total"

if [ "$FAIL_COUNT" -gt 0 ]; then
  echo
  echo "  FAILURES:"
  for f in "${FAILURES[@]:-}"; do
    [ -n "$f" ] && printf "    - %s\n" "$f"
  done
  echo
  echo "  → fix the above before continuing"
  exit 1
fi

echo
echo "  → READY. Next: bash harness/scripts/smoke-tests.sh"
exit 0
