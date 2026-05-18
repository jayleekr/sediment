#!/usr/bin/env bash
# AI Curator — daily ingest
# Schedule: every day at 06:30 KST via launchd plist (see infra/launchd/)
#
# What it does:
#   1. Pull latest from git (fast-forward only)
#   2. Diff against last ingest marker (.curator-last-ingest)
#   3. Run ingest_repo.py — vault-ingester upserts changed files only
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SVC_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
REPO_ROOT="$(cd "$SVC_DIR/../../../.." && pwd)"

cd "$REPO_ROOT"

LAST_MARK="$SVC_DIR/.curator-last-ingest"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

echo "[curator-cron] daily_ingest start at $NOW"
echo "[curator-cron] repo=$REPO_ROOT"

# Optional: pull. Skip if dirty or detached. Local dev typically skips.
if [[ "${CURATOR_DAILY_PULL:-0}" == "1" ]]; then
  if git diff-index --quiet HEAD -- 2>/dev/null; then
    git pull --ff-only || echo "[curator-cron] git pull failed, continuing with current tree"
  else
    echo "[curator-cron] working tree dirty — skipping git pull"
  fi
fi

# Sanity: ingester reachable?
PORT="${VAULT_INGESTER_PORT:-11000}"
if ! curl -fsS "http://localhost:$PORT/healthz" > /dev/null; then
  echo "[curator-cron] vault-ingester not reachable on :$PORT — start it first (make ingester)"
  exit 1
fi

cd "$SVC_DIR"
.venv/bin/python -m scripts.ingest_repo

echo "$NOW" > "$LAST_MARK"
echo "[curator-cron] daily_ingest done"
