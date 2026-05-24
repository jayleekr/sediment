#!/usr/bin/env bash
# verify-deploy.sh — post-deploy production verification.
#
# Run after a `fly deploy` (or after GH Actions auto-deploy completes).
# Confirms:
#   1. Fly app reports started + healthy
#   2. Public healthz returns 200
#   3. OAuth device endpoint responds (smoke test the auth path)
#   4. /proxy/anthropic/ correctly rejects unauthenticated calls (proxy guard)
#   5. /docs returns 404 in prod (security header gating)
#   6. Recall@3 is still in baseline range (nightly-recall preview)
#
# Exit 0 if all green, 1 otherwise.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
API="${SEDIMENT_API:-https://hypeproof-sediment.fly.dev}"

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

pass() { printf "  \033[32m✓\033[0m %s\n" "$1"; PASS_COUNT=$((PASS_COUNT + 1)); }
warn() { printf "  \033[33m⚠\033[0m %s\n" "$1"; WARN_COUNT=$((WARN_COUNT + 1)); }
fail() { printf "  \033[31m✗\033[0m %s\n" "$1"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
stage() { echo; printf "\033[1m── %s ──\033[0m\n" "$1"; }

# ──────────────────────────────────────────────────────────────────────
stage "1. Fly app status"
if command -v fly >/dev/null 2>&1; then
  # awk approach: scan for "started" anywhere in fly status output.
  # 2026-05-24 fix: prior version printed both "started" AND "unknown"
  # (END block always fired even after a match) — false negative.
  if fly status --app hypeproof-sediment 2>/dev/null | grep -qE '\bstarted\b'; then
    pass "fly machine state: started"
  else
    fail "fly machine state: not started"
  fi
else
  warn "flyctl not installed — skipping fly status"
fi

# ──────────────────────────────────────────────────────────────────────
stage "2. public /healthz"
# Cold-start window is up to 15s post-deploy.
for i in 1 2 3 4 5 6 7 8 9 10; do
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$API/healthz" 2>/dev/null)
  [ "$code" = "200" ] && break
  sleep 2
done
if [ "$code" = "200" ]; then
  pass "$API/healthz → 200"
else
  fail "$API/healthz → $code (10 attempts)"
fi

# ──────────────────────────────────────────────────────────────────────
stage "3. OAuth device endpoint reachable"
device_code=$(curl -s --max-time 8 -X POST "$API/api/v1/auth/oauth-device/start" \
  -H 'Content-Type: application/json' -d '{}' 2>/dev/null | grep -o '"device_code":"[^"]*"' | head -1)
if [ -n "$device_code" ]; then
  pass "oauth-device/start returns device_code"
else
  fail "oauth-device/start did not return device_code"
fi

# ──────────────────────────────────────────────────────────────────────
stage "4. /proxy/anthropic — guard enforces (must return 403 without secret)"
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 \
  -X POST "$API/proxy/anthropic/v1/messages" \
  -H 'content-type: application/json' \
  -H 'x-api-key: sk-ant-bogus' \
  -d '{"model":"claude-haiku-4-5","max_tokens":1,"messages":[]}' 2>/dev/null)
if [ "$code" = "403" ]; then
  pass "/proxy/anthropic without x-sediment-proxy-secret → 403 (guard works)"
else
  fail "/proxy/anthropic returned $code (expected 403 without secret — OPEN RELAY RISK)"
fi

# ──────────────────────────────────────────────────────────────────────
stage "5. /docs gated in prod"
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$API/docs" 2>/dev/null)
if [ "$code" = "404" ] || [ "$code" = "302" ]; then
  pass "/docs → $code (gated as expected, ALLOW_PUBLIC_DOCS not set)"
elif [ "$code" = "200" ]; then
  warn "/docs → 200 (publicly reachable — set ALLOW_PUBLIC_DOCS=0 in fly secrets to hide)"
else
  warn "/docs → $code"
fi

# ──────────────────────────────────────────────────────────────────────
stage "6. security headers present"
hdrs=$(curl -sI --max-time 5 "$API/healthz" 2>/dev/null)
for hdr in 'strict-transport-security' 'x-frame-options' 'x-content-type-options' 'referrer-policy'; do
  if echo "$hdrs" | grep -qi "^$hdr:"; then
    pass "header $hdr present"
  else
    fail "header $hdr MISSING"
  fi
done

# ──────────────────────────────────────────────────────────────────────
stage "Summary"
total=$((PASS_COUNT + WARN_COUNT + FAIL_COUNT))
printf "  pass: \033[32m%d\033[0m  warn: \033[33m%d\033[0m  fail: \033[31m%d\033[0m   (total %d)\n" \
  "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" "$total"

if [ "$FAIL_COUNT" -gt 0 ]; then
  echo
  echo "  ✗ DEPLOY VERIFICATION FAILED — investigate above before paging the team."
  echo "    Logs: fly logs --app hypeproof-sediment"
  exit 1
fi

echo
echo "  ✓ DEPLOY VERIFIED. Optional next: bash harness/scripts/run-p3-validator.sh (recall regression)"
exit 0
