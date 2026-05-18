#!/usr/bin/env bash
# lint-sql-cast.sh — guard against the SQLAlchemy `:NAME::TYPE` named-param /
# Postgres-cast collision.
#
# Background (LEARNINGS 2026-05-05 #4 + 2026-05-08 ai_coder_dispatch_real):
#   SQLAlchemy parses `:exp::jsonb` as `:exp` followed by `:jsonb` (two named
#   params). The second is undefined → asyncpg raises PostgresSyntaxError
#   "syntax error at or near \":\"". The bug recurred in 4 files after the
#   one-off 2026-05-05 fix. Codifying it as a lint rule is the only durable
#   prevention.
#
# Allowed cast forms in SQLAlchemy text() queries:
#   CAST(:NAME AS TYPE)           ← preferred
#   CAST(expr AS TYPE)            ← non-param expressions
#   $1::TYPE  / $2::TYPE          ← raw asyncpg numbered (no SQLAlchemy parse)
#
# Forbidden:
#   :NAME::TYPE                   ← collides with SQLAlchemy parser
#
# Usage:
#   bash lint-sql-cast.sh                 # scan whole repo (services/sediment)
#   bash lint-sql-cast.sh <file> [<file>] # scan specific files (called by hook)
#
# Exit codes:
#   0 — no violations
#   1 — violations found (printed to stderr)
#   2 — usage error

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"

# Pattern: a colon, identifier, double-colon, type identifier.
# Examples that match: :p::jsonb, :tid::uuid, :qvec::vector
# Won't match: $1::jsonb (asyncpg native), CAST(:p AS jsonb), x::jsonb (no leading :param)
PATTERN=':[a-zA-Z_][a-zA-Z0-9_]*::[a-zA-Z][a-zA-Z0-9_]*'

# Files to scan. If no args, default to services/sediment. If args, scan those.
files=()
if [ "$#" -gt 0 ]; then
  files=("$@")
else
  # Default scan root: any *.py under services/sediment/ and harness/, excluding
  # virtualenv site-packages (third-party noise like LilyPond/lisp identifiers)
  # and bytecode caches.
  while IFS= read -r f; do
    files+=("$f")
  done < <(
    find "$REPO_ROOT/products/sediment/services/sediment" \
         "$REPO_ROOT/products/sediment/harness" \
         -type d \( -name .venv -o -name __pycache__ -o -name node_modules -o -name .next -o -name .git \) -prune \
         -o -type f -name '*.py' -print 2>/dev/null
  )
fi

violations=0
for f in "${files[@]}"; do
  # Only Python files
  case "$f" in
    *.py) ;;
    *) continue ;;
  esac
  # Existence check (pre-commit may pass deleted files)
  [ -f "$f" ] || continue
  # Skip the lint script's own pattern reference inside this very file (it
  # contains the regex literal). Restrict matches to non-comment-pattern lines.
  if matches=$(grep -nE "$PATTERN" "$f" 2>/dev/null); then
    # Filter out lines that are clearly the lint pattern documentation itself
    # (e.g., the example block in this file or a similar lint doc).
    filtered=$(printf '%s\n' "$matches" | grep -vE '^\s*[0-9]+:\s*#' | grep -vE '(allowed|forbidden|example|pattern):' | grep -vE 'lint-sql-cast' || true)
    if [ -n "$filtered" ]; then
      while IFS= read -r line; do
        echo "  ${f}:${line}" >&2
        violations=$((violations + 1))
      done <<< "$filtered"
    fi
  fi
done

if [ "$violations" -gt 0 ]; then
  cat >&2 <<EOF

[lint-sql-cast] FAIL: $violations occurrences of ':NAME::TYPE' SQL cast.

Use CAST(:NAME AS TYPE) instead. Example:
  Bad:  text("SELECT 1 FROM t WHERE id = :tid::uuid")
  Good: text("SELECT 1 FROM t WHERE id = CAST(:tid AS uuid)")

Background: SQLAlchemy parses :tid::uuid as two params (:tid, :uuid) which
breaks at runtime with PostgresSyntaxError. See:
  products/sediment/harness/ralph/LEARNINGS.md (2026-05-05 #test-04,
  2026-05-08 ai_coder_dispatch_real).
EOF
  exit 1
fi

# Silent on success when called from hook; chatty when called manually.
if [ "$#" -eq 0 ]; then
  echo "[lint-sql-cast] OK: scanned $(printf '%s\n' "${files[@]}" | wc -l | tr -d ' ') files, 0 violations"
fi
exit 0
