#!/usr/bin/env bash
# install-p3-cron.sh — install / reinstall / uninstall the P3 validator launchd job.
#
# Usage:
#   bash install-p3-cron.sh           # install (renders plist from .tmpl with absolute paths)
#   bash install-p3-cron.sh reload    # re-render + reinstall (picks up template edits)
#   bash install-p3-cron.sh uninstall # remove
#   bash install-p3-cron.sh status    # report whether loaded + last run
#
# Renders harness/scripts/com.hypeproof.sediment.p3-validator.plist.tmpl with the
# current REPO_ROOT, HOME, and a per-repo log dir; installs the rendered plist to
# ~/Library/LaunchAgents/. Idempotent. Fixed 2026-05-23 — previously hardcoded
# /Users/jaylee/CodeWorkspace/hypeproof/.claude/worktrees/mvp/products/sediment/
# which doesn't exist after the products/sediment -> sediment rename.

set -uo pipefail

LABEL="com.hypeproof.sediment.p3-validator"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TMPL="$SCRIPT_DIR/${LABEL}.plist.tmpl"
RENDERED="$SCRIPT_DIR/${LABEL}.plist"
DEST_PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG_DIR="$REPO_ROOT/output/cron"
HISTORY="$REPO_ROOT/output/validation/p3-history.log"

render() {
  if [ ! -f "$TMPL" ]; then
    echo "ERR: template missing: $TMPL" >&2
    exit 1
  fi
  mkdir -p "$LOG_DIR"
  # Use a literal-safe sed delimiter (|) since the paths contain no |.
  sed -e "s|__REPO_ROOT__|$REPO_ROOT|g" \
      -e "s|__HOME__|$HOME|g" \
      -e "s|__LOG_DIR__|$LOG_DIR|g" \
      "$TMPL" > "$RENDERED"
}

cmd="${1:-install}"

case "$cmd" in
  install|reload)
    render
    launchctl unload "$DEST_PLIST" 2>/dev/null || true
    cp "$RENDERED" "$DEST_PLIST"
    launchctl load "$DEST_PLIST"
    if [ "$cmd" = "reload" ]; then
      echo "reloaded: $LABEL"
    else
      echo "loaded: $LABEL (daily 09:15)"
    fi
    echo "  repo:    $REPO_ROOT"
    echo "  logs:    $LOG_DIR/sediment-p3-{out,err}.log"
    echo "  history: $HISTORY"
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
      echo "loaded: no  — run: bash install-p3-cron.sh install"
    fi
    if [ -f "$HISTORY" ]; then
      echo
      echo "Last 5 history entries ($HISTORY):"
      tail -5 "$HISTORY"
    else
      echo
      echo "No history yet at $HISTORY"
    fi
    ;;

  *)
    echo "usage: $0 {install|reload|uninstall|status}" >&2
    exit 2
    ;;
esac
