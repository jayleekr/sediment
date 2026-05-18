#!/usr/bin/env bash
# Refresh dashboard.html every 30s. Run in background.
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INTERVAL="${INTERVAL:-30}"

while :; do
  python3 "$SCRIPT_DIR/dashboard.py" >/dev/null 2>&1 || true
  sleep "$INTERVAL"
done
