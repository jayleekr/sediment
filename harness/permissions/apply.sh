#!/usr/bin/env bash
# Apply recommended permission patch to .claude/settings.local.json.
# Run ONCE per machine, BEFORE starting the Ralph loop.
#
# Why this exists:
#   The outer Claude Code session (this REPL) needs permission to run docker,
#   chmod, plutil, etc. Without this patch, every command triggers a prompt.
#   The Ralph loop subprocess runs with --dangerously-skip-permissions and
#   does NOT need these — but the wrapper that starts/stops/inspects it does.
#
# Usage:
#   ./harness/permissions/apply.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RECOMMENDED="$REPO_ROOT/harness/permissions/recommended.local.json"
TARGET="$REPO_ROOT/.claude/settings.local.json"

if ! command -v jq >/dev/null 2>&1; then
  echo "error: jq required" >&2
  exit 1
fi

if [ ! -f "$TARGET" ]; then
  echo "{}" > "$TARGET"
fi

echo "Backing up to $TARGET.bak"
cp "$TARGET" "$TARGET.bak"

echo "Merging recommended permissions..."
# Strip the _comment field, then deep-merge with existing
jq -s '
  (.[0] | del(._comment)) as $rec
  | .[1] as $cur
  | $cur * $rec
  | .permissions.allow = (((($cur.permissions.allow // []) + ($rec.permissions.allow // [])) | unique))
  | .permissions.deny  = (((($cur.permissions.deny  // []) + ($rec.permissions.deny  // [])) | unique))
' "$RECOMMENDED" "$TARGET" > "$TARGET.new"

mv "$TARGET.new" "$TARGET"

echo "Applied. New allow rules: $(jq '.permissions.allow | length' "$TARGET")"
echo "         deny rules:  $(jq '.permissions.deny | length' "$TARGET")"
echo
echo "Restart Claude Code session to pick up new rules."
echo "Backup: $TARGET.bak"
