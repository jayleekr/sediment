#!/usr/bin/env bash
# install-p3-cron.sh — install / reinstall / uninstall the P3 validator launchd job.
#
# Usage:
#   bash install-p3-cron.sh           # install
#   bash install-p3-cron.sh reload    # reinstall (unload then load — picks up plist edits)
#   bash install-p3-cron.sh uninstall # remove
#   bash install-p3-cron.sh status    # report whether loaded + last run
#
# Idempotent — safe to run repeatedly. No external deps beyond launchctl.

set -uo pipefail

LABEL="com.hypeproof.sediment.p3-validator"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_PLIST="$SCRIPT_DIR/${LABEL}.plist"
DEST_PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

cmd="${1:-install}"

case "$cmd" in
  install)
    if [ ! -f "$SRC_PLIST" ]; then
      echo "ERR: source plist missing: $SRC_PLIST" >&2
      exit 1
    fi
    cp "$SRC_PLIST" "$DEST_PLIST"
    launchctl unload "$DEST_PLIST" 2>/dev/null || true
    launchctl load "$DEST_PLIST"
    echo "loaded: $LABEL (daily 09:15)"
    ;;

  reload)
    if [ ! -f "$DEST_PLIST" ]; then
      cp "$SRC_PLIST" "$DEST_PLIST"
    fi
    launchctl unload "$DEST_PLIST" 2>/dev/null || true
    cp "$SRC_PLIST" "$DEST_PLIST"
    launchctl load "$DEST_PLIST"
    echo "reloaded: $LABEL"
    ;;

  uninstall)
    launchctl unload "$DEST_PLIST" 2>/dev/null || true
    rm -f "$DEST_PLIST"
    echo "uninstalled: $LABEL"
    ;;

  status)
    if launchctl list | grep -q "$LABEL"; then
      echo "loaded: yes"
      launchctl list "$LABEL" | head -30
    else
      echo "loaded: no"
    fi
    HISTORY="/Users/jaylee/CodeWorkspace/hypeproof/.claude/worktrees/mvp/products/sediment/output/validation/p3-history.log"
    if [ -f "$HISTORY" ]; then
      echo
      echo "Last 5 history entries:"
      tail -5 "$HISTORY"
    fi
    ;;

  *)
    echo "usage: $0 {install|reload|uninstall|status}" >&2
    exit 2
    ;;
esac
