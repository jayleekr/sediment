#!/usr/bin/env bash
# restart-services-if-changed.sh
#
# Bounce long-lived uvicorn services whose source files have changed since a
# baseline ref. Called by ai-commit.sh `gate` step BEFORE running validator
# (LEARNINGS 2026-05-08 pattern=service_restart_propagation — without this, the
# coder's gate measures only static delta and under-reports the true score
# improvement, sometimes causing fixes that actually work post-restart to be
# treated as no-ops).
#
# Usage:
#   bash restart-services-if-changed.sh [BASELINE_REF]
#
# BASELINE_REF defaults to HEAD~1. Pass the SHA recorded by ai-commit.sh begin
# (`output/ai-commit/<CHECK_ID>.base`) for accurate diff scope.
#
# Strategy:
#   1. Compute changed files since BASELINE_REF (committed + working tree).
#   2. Map each path to one of: platform, langgraph, ingester, metadata, none.
#   3. For each affected service, kill its current uvicorn pid and re-launch
#      using the EXACT cmdline from `ps -o args=` (no hardcoded module/port
#      assumptions — pickup from runtime).
#   4. Wait up to 15s per service for /healthz to return 200.
#
# Idempotent: services already up + no changes => no-op. Services down => start
# them via the recorded cmdline patterns.
#
# Exit 0 always (best-effort — never block validator gate).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
SVC_DIR="$REPO_ROOT/products/sediment/services/sediment"

BASELINE_REF="${1:-HEAD~1}"

cd "$REPO_ROOT"

# Resolve baseline — accept SHA, branch, or HEAD~N. If invalid, fall back to
# HEAD~1 silently.
if ! git rev-parse --verify "$BASELINE_REF" >/dev/null 2>&1; then
  echo "[restart-services] WARN: baseline '$BASELINE_REF' not a git ref, using HEAD~1" >&2
  BASELINE_REF="HEAD~1"
fi

# Collect changed files (committed since baseline) UNION (working tree).
# Limit scope to services/sediment/ — other paths don't affect uvicorn services.
CHANGED=$(
  {
    git diff --name-only "$BASELINE_REF"...HEAD -- products/sediment/services/sediment/
    git diff --name-only -- products/sediment/services/sediment/
    git diff --cached --name-only -- products/sediment/services/sediment/
  } | sort -u
)

if [ -z "$CHANGED" ]; then
  echo "[restart-services] no source changes since $BASELINE_REF — skipping bounce"
  exit 0
fi

echo "[restart-services] changed files since $BASELINE_REF:"
echo "$CHANGED" | sed 's/^/  /'

# Map files to services. Shared libs (lab_platform, lab_lib) trigger ALL
# services since they're imported widely.
need_platform=0
need_langgraph=0
need_ingester=0
need_metadata=0

while IFS= read -r f; do
  [ -z "$f" ] && continue
  case "$f" in
    products/sediment/services/sediment/applications/sediment_platform/*)
      need_platform=1 ;;
    products/sediment/services/sediment/applications/sediment_langgraph/*)
      need_langgraph=1 ;;
    products/sediment/services/sediment/applications/vault_ingester/*)
      need_ingester=1 ;;
    products/sediment/services/sediment/applications/metadata_svc/*)
      need_metadata=1 ;;
    products/sediment/services/sediment/lab_platform/* | \
    products/sediment/services/sediment/lab_lib/*)
      # shared libs imported by all services
      need_platform=1; need_langgraph=1; need_ingester=1; need_metadata=1 ;;
    products/sediment/services/sediment/validator/* | \
    products/sediment/services/sediment/scripts/* | \
    products/sediment/services/sediment/tests/*)
      ;;  # validator/scripts/tests don't run as long-lived services
  esac
done <<< "$CHANGED"

echo "[restart-services] services to bounce: platform=$need_platform langgraph=$need_langgraph ingester=$need_ingester metadata=$need_metadata"

# Bounce one service identified by an `applications.X.main:app` substring.
# Reads the current uvicorn cmdline so we don't hardcode port/module.
bounce() {
  local module_pattern="$1"  # e.g. "applications.sediment_platform.main"
  local fallback_app="$2"    # e.g. "applications.sediment_platform.main:app"
  local fallback_port="$3"   # e.g. 10100
  local nice_name="$4"       # e.g. "curator-platform"

  local pid cmd
  pid=$(pgrep -f "uvicorn.*${module_pattern}" | head -1 || true)

  if [ -n "$pid" ]; then
    cmd=$(ps -p "$pid" -o args= 2>/dev/null | sed -E 's/^[[:space:]]*//')
    echo "  bouncing $nice_name (pid=$pid)"
    kill "$pid" 2>/dev/null || true
    # Wait for socket to free
    for _ in 1 2 3 4 5; do
      if ! kill -0 "$pid" 2>/dev/null; then break; fi
      sleep 0.5
    done
    # Relaunch with the captured cmdline (preserves any custom flags)
    cd "$SVC_DIR"
    nohup bash -c "$cmd" > "/tmp/curator-${nice_name}.log" 2>&1 &
    cd "$REPO_ROOT"
  else
    echo "  $nice_name not running — starting fresh on :$fallback_port"
    cd "$SVC_DIR"
    nohup .venv/bin/uvicorn "$fallback_app" --port "$fallback_port" \
      > "/tmp/curator-${nice_name}.log" 2>&1 &
    cd "$REPO_ROOT"
  fi

  # Wait for healthz (max 15s)
  for i in $(seq 1 15); do
    if curl -sf "http://localhost:$fallback_port/healthz" >/dev/null 2>&1; then
      echo "  $nice_name healthy on :$fallback_port (${i}s)"
      return 0
    fi
    sleep 1
  done
  echo "  WARN: $nice_name not healthy on :$fallback_port within 15s — see /tmp/curator-${nice_name}.log" >&2
  return 1
}

[ "$need_platform" = "1" ]  && bounce "applications.sediment_platform.main"  "applications.sediment_platform.main:app"  10100 "curator-platform"
[ "$need_langgraph" = "1" ] && bounce "applications.sediment_langgraph.main" "applications.sediment_langgraph.main:app" 10020 "curator-langgraph"
[ "$need_ingester" = "1" ]  && bounce "applications.vault_ingester.main"    "applications.vault_ingester.main:app"    11000 "vault-ingester"
[ "$need_metadata" = "1" ]  && bounce "applications.metadata_svc.main"      "applications.metadata_svc.main:app"      12000 "metadata-svc"

echo "[restart-services] bounce complete"
exit 0
