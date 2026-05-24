#!/usr/bin/env bash
# deploy-check.sh — pre-deploy readiness audit.
#
# Answers: "if I push to main right now, will fly-deploy.yml succeed AND
# will the new image actually boot?" Catches the failure modes that
# bit us in this remediation pass:
#
#   1. New hard-required secrets not set on Fly → start.sh refuses to boot
#   2. pyproject.toml dep added but venv not synced → cli-tests FAIL
#   3. Migration written but not applied → 500 on first webhook
#   4. Cargo.toml changed but binary not rebuilt → CLI smoke fail
#   5. nginx.conf changed but envsubst placeholder not handled
#
# Output: PASS / WARN / FAIL with concrete next-action per finding.
# Exit 0 if no FAIL, 1 otherwise.
#
# Usage:
#   bash harness/scripts/deploy-check.sh        # full check
#   bash harness/scripts/deploy-check.sh --skip-fly  # skip flyctl calls (offline)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SVC="$REPO_ROOT/services/sediment"

SKIP_FLY=0
for arg in "$@"; do
  [ "$arg" = "--skip-fly" ] && SKIP_FLY=1
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
stage "1. Fly secrets — boot-required keys"
# start.sh hard-requires these (`: \"${X:?...}\"`). Without them the
# container refuses to start → deploy reports "failed".
REQUIRED_SECRETS="JWT_SECRET SEDIMENT_ONBOARDING_SECRET ANTHROPIC_PROXY_SECRET GITHUB_WEBHOOK_SECRET"
RECOMMENDED_SECRETS="STRIPE_WEBHOOK_SECRET ANTHROPIC_API_KEY OPENAI_API_KEY GOOGLE_API_KEY"
if [ "$SKIP_FLY" = "1" ]; then
  warn "--skip-fly — Fly secret check skipped"
elif ! command -v fly >/dev/null 2>&1; then
  warn "flyctl not installed — install via brew install flyctl"
else
  if ! fly auth whoami --app hypeproof-sediment >/dev/null 2>&1; then
    warn "flyctl not authenticated — run: fly auth login"
  else
    # `fly secrets list` shows names + digests, never values.
    SECRETS_LIST=$(fly secrets list --app hypeproof-sediment 2>/dev/null)
    for s in $REQUIRED_SECRETS; do
      if echo "$SECRETS_LIST" | grep -qE "^${s}\s"; then
        pass "fly secret $s present"
      else
        fail "fly secret $s MISSING — fly secrets set $s=..."
      fi
    done
    for s in $RECOMMENDED_SECRETS; do
      if echo "$SECRETS_LIST" | grep -qE "^${s}\s"; then
        pass "fly secret $s present"
      else
        warn "fly secret $s missing (recommended)"
      fi
    done
  fi
fi

# ──────────────────────────────────────────────────────────────────────
stage "2. pyproject.toml ↔ venv sync"
if [ -x "$SVC/.venv/bin/python" ]; then
  # Stripe was just added — canary check for "I added a dep but forgot install".
  if "$SVC/.venv/bin/python" -c "import stripe" 2>/dev/null; then
    pass "stripe importable (pyproject sync OK)"
  else
    fail "stripe in pyproject.toml but not in venv — make install"
  fi
else
  fail "$SVC/.venv missing — make install"
fi

# ──────────────────────────────────────────────────────────────────────
stage "3. pending migrations"
MIGR_DIR="$REPO_ROOT/infra/migrations"
mig_count=$(find "$MIGR_DIR" -maxdepth 1 -name '*.sql' 2>/dev/null | wc -l | tr -d ' ')
echo "  found $mig_count migration file(s) in infra/migrations/"
# We can't know which migrations are applied without a DB connection;
# show the list so Jay can mentally diff against prod.
for f in "$MIGR_DIR"/*.sql; do
  [ -f "$f" ] || continue
  echo "    - $(basename "$f")"
done
# Heuristic: any migration file modified in last 24h is likely unapplied.
recent=$(find "$MIGR_DIR" -maxdepth 1 -name '*.sql' -mtime -1 2>/dev/null)
if [ -n "$recent" ]; then
  warn "migration(s) added/modified in last 24h — apply via psql before deploy:"
  for f in $recent; do
    echo "      psql \$DATABASE_URL_SERVICE < $f"
  done
else
  pass "no migrations modified in last 24h (still verify against prod)"
fi

# ──────────────────────────────────────────────────────────────────────
stage "4. Rust CLI cargo check"
if command -v cargo >/dev/null 2>&1; then
  if (cd "$SVC/../sediment-cli" && cargo check --quiet 2>/dev/null); then
    pass "sediment-cli cargo check clean"
  else
    fail "sediment-cli cargo check FAILED — cd services/sediment-cli && cargo check"
  fi
else
  warn "cargo not on PATH — install via rustup"
fi

# ──────────────────────────────────────────────────────────────────────
stage "5. Dockerfile + nginx envsubst placeholders"
# nginx.conf is now a template — start.sh runs envsubst at boot. Verify
# every placeholder is declared in the envsubst whitelist in start.sh.
NGINX="$REPO_ROOT/infra/deploy/nginx.conf"
START_SH="$REPO_ROOT/infra/deploy/start.sh"
if [ -f "$NGINX" ] && [ -f "$START_SH" ]; then
  placeholders=$(grep -oE '\$\{[A-Z_]+\}' "$NGINX" | sort -u)
  whitelist=$(grep -oE 'envsubst.*' "$START_SH" | grep -oE '\$\{[A-Z_]+\}' | sort -u)
  missing=""
  for p in $placeholders; do
    if ! echo "$whitelist" | grep -qF "$p"; then
      missing="$missing $p"
    fi
  done
  if [ -z "$missing" ]; then
    pass "nginx.conf placeholders all in start.sh envsubst whitelist"
  else
    fail "nginx.conf has placeholders NOT in start.sh envsubst whitelist:$missing"
  fi
fi

# ──────────────────────────────────────────────────────────────────────
stage "6. Local validator P0 (last-resort gate before push)"
if cd "$REPO_ROOT" && make validate-p0 >/tmp/deploy-p0.log 2>&1; then
  score=$(grep -E 'weight' /tmp/deploy-p0.log | head -1 | tr -d ' ' | tr -s ' ')
  pass "validate-p0 PASS — $score"
else
  fail "validate-p0 FAIL — see /tmp/deploy-p0.log"
  tail -6 /tmp/deploy-p0.log | sed 's/^/    /'
fi

# ──────────────────────────────────────────────────────────────────────
stage "7. last Fly release status (don't deploy on top of a broken one)"
if [ "$SKIP_FLY" = "1" ] || ! command -v fly >/dev/null 2>&1; then
  warn "skipping fly release status check"
else
  last_status=$(fly releases --app hypeproof-sediment 2>/dev/null | awk 'NR==3 {print $4}')
  case "$last_status" in
    complete)
      pass "last fly release: complete"
      ;;
    failed)
      warn "last fly release: FAILED — debug before redeploy: fly logs --app hypeproof-sediment"
      ;;
    pending|deploying)
      fail "last fly release: $last_status — wait for it to settle"
      ;;
    "")
      warn "could not parse fly releases output"
      ;;
    *)
      warn "last fly release: $last_status"
      ;;
  esac
fi

# ──────────────────────────────────────────────────────────────────────
stage "Summary"
total=$((PASS_COUNT + WARN_COUNT + FAIL_COUNT))
printf "  pass: \033[32m%d\033[0m  warn: \033[33m%d\033[0m  fail: \033[31m%d\033[0m   (total %d)\n" \
  "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" "$total"

if [ "$FAIL_COUNT" -gt 0 ]; then
  echo
  echo "  ✗ NOT READY TO DEPLOY:"
  for f in "${FAILURES[@]:-}"; do
    [ -n "$f" ] && printf "    - %s\n" "$f"
  done
  exit 1
fi

echo
echo "  ✓ READY TO DEPLOY. Next: git push origin main (triggers fly-deploy.yml)"
exit 0
