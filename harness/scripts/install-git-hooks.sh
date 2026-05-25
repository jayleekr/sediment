#!/usr/bin/env bash
# install-git-hooks.sh — wire harness gates into git pre-commit + pre-push.
#
# What it installs:
#   .git/hooks/pre-commit  → lint-sql-cast + check-secrets (staged)
#   .git/hooks/pre-push    → check-secrets (--diff origin/main)
#
# Both are SHIMS (4-line wrappers) that call into harness/scripts/. The
# repo-tracked scripts are the source of truth; hooks are just entry points.
# Idempotent — overwrites prior hooks of the same name.
#
# Usage:
#   bash harness/scripts/install-git-hooks.sh           # install
#   bash harness/scripts/install-git-hooks.sh --uninstall

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
HOOKS_DIR="$REPO_ROOT/.git/hooks"

if [ ! -d "$HOOKS_DIR" ]; then
  echo "ERR: $HOOKS_DIR not found — is this a git repo?" >&2
  exit 1
fi

# Allow --uninstall
if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$HOOKS_DIR/pre-commit" "$HOOKS_DIR/pre-push"
  echo "uninstalled pre-commit + pre-push hooks"
  exit 0
fi

# pre-commit: catch SQL cast + staged secrets BEFORE commit lands.
cat > "$HOOKS_DIR/pre-commit" <<'EOF'
#!/usr/bin/env bash
# Auto-installed by harness/scripts/install-git-hooks.sh — do not edit.
# Bypass with `git commit --no-verify` (logged).
set -e
ROOT="$(git rev-parse --show-toplevel)"
echo "[pre-commit] lint-sql-cast..."
bash "$ROOT/harness/scripts/lint-sql-cast.sh" || {
  echo "[pre-commit] BLOCKED — :NAME::TYPE pattern found. Use CAST(:NAME AS TYPE)."
  exit 1
}
echo "[pre-commit] check-secrets (staged)..."
bash "$ROOT/harness/scripts/check-secrets.sh" || {
  echo "[pre-commit] BLOCKED — secret pattern detected in staged files."
  echo "             Remove the secret, then re-stage. False positive?"
  echo "             Add path to EXCLUDED_PATHS in harness/scripts/check-secrets.sh."
  exit 1
}
echo "[pre-commit] OK"
EOF
chmod +x "$HOOKS_DIR/pre-commit"

# pre-push: scan all commits being pushed (diff against upstream).
cat > "$HOOKS_DIR/pre-push" <<'EOF'
#!/usr/bin/env bash
# Auto-installed by harness/scripts/install-git-hooks.sh — do not edit.
# Bypass with `git push --no-verify` (logged).
set -e
ROOT="$(git rev-parse --show-toplevel)"
# Read refs from stdin (git push protocol). Each line:
#   <local ref> <local sha> <remote ref> <remote sha>
# We only care about the local sha + the remote sha to diff against.
while read -r local_ref local_sha remote_ref remote_sha; do
  [ -z "$local_sha" ] && continue
  # New branch (no remote sha) — scan everything new vs the default branch
  # to keep the scan bounded (full repo otherwise).
  if [ "$remote_sha" = "0000000000000000000000000000000000000000" ]; then
    # Try to resolve origin/main; if absent, fall back to scanning the last 50 commits.
    if git rev-parse --verify origin/main >/dev/null 2>&1; then
      base="origin/main"
    else
      base="$local_sha~50"
    fi
  else
    base="$remote_sha"
  fi
  echo "[pre-push] check-secrets --diff $base..."
  bash "$ROOT/harness/scripts/check-secrets.sh" --diff "$base" || {
    echo "[pre-push] BLOCKED — secret pattern in outgoing commits."
    echo "             Rotate the secret + rewrite history (git filter-repo) before push."
    exit 1
  }
done
echo "[pre-push] OK"
EOF
chmod +x "$HOOKS_DIR/pre-push"

echo "installed git hooks:"
echo "  $HOOKS_DIR/pre-commit"
echo "  $HOOKS_DIR/pre-push"
echo
echo "To bypass (emergency only): git commit --no-verify / git push --no-verify"
echo "To uninstall:               bash harness/scripts/install-git-hooks.sh --uninstall"
