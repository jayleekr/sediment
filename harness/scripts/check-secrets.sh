#!/usr/bin/env bash
# check-secrets.sh — scan files for secrets / credentials / private keys.
#
# Catches the common-case leak vectors before they land in git history.
# Designed to run from:
#   - git pre-commit hook (against staged files)
#   - git pre-push hook (against commits-to-push)
#   - GH Actions PR job (against pull-request diff)
#   - manual full-history audit (before flipping repo public)
#
# Modes:
#   ./check-secrets.sh                    # scan staged files (pre-commit default)
#   ./check-secrets.sh --diff <base>      # files changed since <base> (commit/branch)
#   ./check-secrets.sh --files <paths..>  # explicit file list
#   ./check-secrets.sh --history          # scan ALL commits in git history
#                                          (slow — use before going public)
#   ./check-secrets.sh --working-tree     # current uncommitted state
#
# Exit codes:
#   0 — clean
#   1 — secrets/dangerous paths detected
#   2 — usage error
#
# Patterns are intentionally conservative — high precision, modest recall.
# Stuff that's obviously a real credential blocks; anything ambiguous is
# logged as a warning but doesn't block. False positives are worse than
# false negatives here because the cost of blocking a legit commit is
# friction, but the cost of leaking a real secret is "rotate everything".

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# ── Dangerous file paths (globs) ──────────────────────────────────────
# These should NEVER be in git, regardless of content.
DANGEROUS_PATHS=(
  '.env'
  '.env.local'
  '.env.production'
  '.env.development'
  '*.pem'
  '*.key'
  '*.p12'
  '*.pfx'
  'credentials.json'
  'service-account*.json'
  '.aws/credentials'
  '.ssh/id_*'
  '.ssh/*_rsa'
  '.ssh/*_ed25519'
  '.npmrc'
  '.netrc'
  'kubeconfig'
)

# Files that LOOK dangerous but are actually safe (allowlist).
ALLOWED_PATHS=(
  '.env.example'
  '.env.sample'
  '.env.template'
  '.env.test'
)

# ── Content patterns ─────────────────────────────────────────────────
# Each is a Perl-compatible regex matching real-secret shapes.
# Pattern label : regex
SECRET_PATTERNS=(
  'AWS access key|AKIA[0-9A-Z]{16}'
  'AWS secret access key|aws_secret_access_key\s*=\s*[A-Za-z0-9/+=]{40}'
  'Stripe live key|sk_live_[0-9a-zA-Z]{24,}'
  'Stripe restricted key|rk_live_[0-9a-zA-Z]{24,}'
  'Stripe webhook secret|whsec_[0-9a-zA-Z]{32,}'
  'GitHub PAT (classic)|ghp_[0-9a-zA-Z]{36}'
  'GitHub OAuth|gho_[0-9a-zA-Z]{36}'
  'GitHub user PAT|github_pat_[0-9a-zA-Z_]{82}'
  'GitHub App|ghs_[0-9a-zA-Z]{36}'
  'Slack bot token|xoxb-[0-9]{10,}-[0-9]{10,}-[0-9a-zA-Z]{24,}'
  'Slack user token|xoxp-[0-9]{10,}-[0-9]{10,}-[0-9]{10,}-[0-9a-zA-Z]{32,}'
  'Slack webhook|hooks\.slack\.com/services/T[0-9A-Z]{8,}/B[0-9A-Z]{8,}/[0-9a-zA-Z]{20,}'
  'Discord bot token|[MN][A-Za-z0-9_-]{23}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}'
  'OpenAI API key|sk-[A-Za-z0-9]{40,}'
  'OpenAI project key|sk-proj-[A-Za-z0-9_-]{40,}'
  'Anthropic API key|sk-ant-(api03|admin01)-[A-Za-z0-9_-]{80,}'
  'Google API key|AIza[0-9A-Za-z_-]{35}'
  'Gemini key|AIza[0-9A-Za-z_-]{35}'
  'PEM private key|-----BEGIN (RSA|DSA|EC|OPENSSH|PGP) PRIVATE KEY-----'
  'PKCS8 private key|-----BEGIN PRIVATE KEY-----'
  'SSH private key|-----BEGIN OPENSSH PRIVATE KEY-----'
  'JWT (looks real, with 3 base64 segments)|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}'
  # DB URL patterns: must not catch placeholder values (<pwd>, <secret>) or
  # documented local-dev creds (curator_*_local, _local_dev). Negative-look
  # via a "not-a-placeholder" character class would be cleaner but ERE
  # doesn't support lookaround — we instead post-filter via ALLOWED_VALUES.
  'Postgres URL with password|postgres(ql)?(\+[a-z]+)?://[^:]+:[^@/]{4,}@'
  'MySQL URL with password|mysql://[^:]+:[^@/]{4,}@'
  'MongoDB URL with password|mongodb(\+srv)?://[^:]+:[^@/]{4,}@'
  'Generic api_key= long literal|api[_-]?key\s*=\s*["'\''][A-Za-z0-9_-]{32,}["'\'']'
  'Discord webhook URL|discord(app)?\.com/api/webhooks/[0-9]+/[A-Za-z0-9_-]{30,}'
)

# Substrings whose presence in a matched value means "this is NOT a real
# secret" — placeholder text, doc examples, known local-dev creds. Applied
# AFTER the regex matches but BEFORE flagging. Keep this list small and
# meaningful — broad entries here weaken the scanner.
ALLOWED_VALUE_SUBSTRINGS=(
  '<pwd>'                                # docs placeholder
  '<password>'                           # docs placeholder
  '<secret>'                             # docs placeholder
  '<superuser-pwd>'                      # docs placeholder
  '<token>'                              # docs placeholder
  'CHANGE_ME'                            # placeholder convention
  'replace-with'                         # placeholder convention
  'your-'                                # placeholder convention
  'changeme'                             # placeholder convention
  'curator_local_dev'                    # Sediment local docker-compose creds
  'curator_app_local'                    # Sediment local docker-compose creds
  'curator_service_local'                # Sediment local docker-compose creds
  'sediment_app_local'                   # Sediment local docker-compose creds
  'sediment_service_local'               # Sediment local docker-compose creds
)

# Files where matching is expected (this script itself, test fixtures,
# .env.example documentation strings, the LEARNINGS catalog that quotes
# patterns, etc.). Path-based excludes.
EXCLUDED_PATHS=(
  'harness/scripts/check-secrets.sh'           # self
  'harness/ralph/LEARNINGS.md'                 # quotes patterns
  '*.example'
  '*.sample'
  '*.template'
  '.env.example'
  '.gitignore'
  'output/reviews/**'                          # review reports may quote
  'docs/runbooks/**'                           # runbooks document patterns
  '.claude/skills/**'                          # external skills (gstack)
)

# ──────────────────────────────────────────────────────────────────────

usage() {
  cat <<EOF >&2
Usage: $0 [mode] [args]

Modes:
  (no args)              scan staged files (default — pre-commit)
  --diff <base>          files changed since <base> (e.g., origin/main, HEAD~3)
  --files <paths..>      explicit file list
  --history              scan EVERY commit in git history (slow, pre-public)
  --working-tree         current uncommitted state (tracked + untracked)

Exit: 0 = clean, 1 = secrets found, 2 = bad args
EOF
  exit 2
}

# Path-glob match (bash-3.2 compatible — uses `case` not `[[`)
path_matches_glob() {
  local path="$1"; local glob="$2"
  # shellcheck disable=SC2254
  case "$path" in $glob) return 0 ;; *) return 1 ;; esac
}

is_excluded() {
  local path="$1"
  for glob in "${EXCLUDED_PATHS[@]}"; do
    if path_matches_glob "$path" "$glob"; then return 0; fi
    # Also match basename for glob without slash
    case "$glob" in
      */*) ;;
      *) if path_matches_glob "$(basename "$path")" "$glob"; then return 0; fi ;;
    esac
  done
  return 1
}

is_allowed_path() {
  local path="$1"
  for ok in "${ALLOWED_PATHS[@]}"; do
    if [ "$(basename "$path")" = "$ok" ]; then return 0; fi
  done
  return 1
}

is_dangerous_path() {
  local path="$1"
  is_allowed_path "$path" && return 1
  for glob in "${DANGEROUS_PATHS[@]}"; do
    if path_matches_glob "$(basename "$path")" "$glob"; then return 0; fi
    if path_matches_glob "$path" "$glob"; then return 0; fi
  done
  return 1
}

# Module-level counter (incremented by scan_content via stdout-capture-and-eval).
# Bash 3.2 doesn't have nameref, so we use a temp file.
_FINDINGS_FILE=$(mktemp)
trap 'rm -f "$_FINDINGS_FILE"' EXIT

# True if the matched sample contains a known allowed substring
# (placeholder, doc example, local-dev cred).
_is_allowed_value() {
  local sample="$1"
  for s in "${ALLOWED_VALUE_SUBSTRINGS[@]}"; do
    case "$sample" in
      *"$s"*) return 0 ;;
    esac
  done
  return 1
}

# Scan a single file's content for any secret pattern.
# Args: label (for output), content (string)
scan_content() {
  local label="$1"
  local content="$2"
  for entry in "${SECRET_PATTERNS[@]}"; do
    local name="${entry%%|*}"
    local rx="${entry#*|}"
    # First check ANY match — most files trip nothing, so the cheap test wins.
    if printf '%s' "$content" | grep -E -q "$rx" 2>/dev/null; then
      # Then walk each match line and apply the allowlist post-filter.
      while IFS= read -r sample; do
        [ -z "$sample" ] && continue
        if _is_allowed_value "$sample"; then continue; fi
        local short
        short=$(printf '%s' "$sample" | cut -c1-60)
        printf '  \033[31m✗\033[0m %s — %s: %s...\n' "$label" "$name" "$short" >&2
        echo "1" >> "$_FINDINGS_FILE"
      done < <(printf '%s' "$content" | grep -E -o "$rx" 2>/dev/null)
    fi
  done
}

# Resolve a path arg to an absolute readable file. Accepts both repo-relative
# and absolute paths. Returns empty if file doesn't exist.
_resolve_path() {
  local p="$1"
  if [ -f "$p" ]; then printf '%s' "$p"; return; fi
  if [ -f "$REPO_ROOT/$p" ]; then printf '%s' "$REPO_ROOT/$p"; return; fi
  printf ''
}

# Quick extension-based skip — `file` command is ~50ms per call which adds
# up over hundreds of files. Skip obvious binaries by extension first.
_skip_by_extension() {
  case "$1" in
    *.png|*.jpg|*.jpeg|*.gif|*.svg|*.webp|*.ico|*.bmp|*.tiff) return 0 ;;
    *.mp4|*.mov|*.webm|*.avi|*.mkv|*.mp3|*.wav|*.ogg|*.m4a) return 0 ;;
    *.pdf|*.docx|*.xlsx|*.pptx|*.odt|*.ods|*.odp) return 0 ;;
    *.zip|*.tar|*.gz|*.bz2|*.xz|*.7z|*.rar) return 0 ;;
    *.whl|*.egg|*.dylib|*.so|*.dll|*.exe|*.bin|*.o) return 0 ;;
    *.ttf|*.otf|*.woff|*.woff2|*.eot) return 0 ;;
    *.pyc|*.pyo|*.class) return 0 ;;
    *.metadata.json) return 0 ;;  # asset metadata sidecars
  esac
  return 1
}

# Is this file binary? Use `file` command as fallback for extensionless files
# or unrecognized extensions.
_is_binary() {
  local f="$1"
  _skip_by_extension "$f" && return 0
  local mime
  mime=$(file -b --mime-encoding "$f" 2>/dev/null)
  case "$mime" in
    binary|application/octet-stream*) return 0 ;;
    *) return 1 ;;
  esac
}

scan_files() {
  local total_files=0
  for path in "$@"; do
    [ -z "$path" ] && continue
    total_files=$((total_files + 1))

    # Path-level check (cheap, blocks dangerous paths regardless of content).
    if is_dangerous_path "$path" && ! is_excluded "$path"; then
      printf '  \033[31m✗\033[0m %s — DANGEROUS PATH (must never be committed)\n' "$path" >&2
      echo "1" >> "$_FINDINGS_FILE"
      continue
    fi
    is_excluded "$path" && continue

    local abs
    abs=$(_resolve_path "$path")
    [ -z "$abs" ] && continue

    # Skip >1MB (binary/generated/lockfiles)
    local size
    size=$(stat -f%z "$abs" 2>/dev/null || stat -c%s "$abs" 2>/dev/null || echo 0)
    [ "$size" -gt 1048576 ] && continue

    _is_binary "$abs" && continue

    local content
    content=$(cat "$abs" 2>/dev/null)
    scan_content "$path" "$content"
  done

  local total_findings
  total_findings=$(wc -l < "$_FINDINGS_FILE" 2>/dev/null | tr -d ' ')
  total_findings=${total_findings:-0}

  if [ "$total_findings" -gt 0 ]; then
    echo >&2
    printf "\033[31m✗\033[0m %d finding(s) across %d file(s).\n" "$total_findings" "$total_files" >&2
    echo "If this is a false positive, add the path to EXCLUDED_PATHS in check-secrets.sh." >&2
    return 1
  fi
  echo "OK — scanned $total_files file(s), no secrets found."
  return 0
}

scan_history() {
  # Fast history scan: single combined regex against `git log -p`. The
  # earlier per-line nested for-loop took 20+ minutes on a 130-commit repo
  # because it spawned grep per (line × pattern). One grep with an OR-joined
  # regex finishes in seconds.
  local rev_count
  rev_count=$(git -C "$REPO_ROOT" rev-list --count HEAD 2>/dev/null || echo 0)
  echo "Full-history scan ($rev_count commits, single grep pass)..." >&2

  # Build one big alternation. Named-group capture would be nice but POSIX
  # ERE doesn't support naming — we'll re-scan a hit to pin the pattern name.
  local combined=""
  for entry in "${SECRET_PATTERNS[@]}"; do
    local rx="${entry#*|}"
    if [ -z "$combined" ]; then
      combined="$rx"
    else
      combined="${combined}|${rx}"
    fi
  done

  # Run `git log -p --all` and filter to lines starting with `+` (added
  # content) that match ANY pattern. Streamed — constant memory.
  local hits
  hits=$(
    git -C "$REPO_ROOT" log --all -p --no-color --diff-filter=AM 2>/dev/null \
    | grep -E "^\+.*($combined)" \
    | grep -v '^+++' \
    | head -200
  )

  if [ -z "$hits" ]; then
    echo "OK — full history clean across $rev_count commits."
    return 0
  fi

  local total_findings=0
  # Re-attribute hits to pattern names + apply ALLOWED_VALUE_SUBSTRINGS filter.
  echo "$hits" | while IFS= read -r line; do
    [ -z "$line" ] && continue
    for entry in "${SECRET_PATTERNS[@]}"; do
      local name="${entry%%|*}"
      local rx="${entry#*|}"
      local sample
      sample=$(printf '%s' "$line" | grep -E -o "$rx" 2>/dev/null | head -1)
      [ -z "$sample" ] && continue
      if _is_allowed_value "$sample"; then continue; fi
      local short
      short=$(printf '%s' "$sample" | cut -c1-60)
      printf '  \033[31m✗\033[0m %s: %s...\n' "$name" "$short" >&2
      echo "1" >> "$_FINDINGS_FILE"
      break
    done
  done

  total_findings=$(wc -l < "$_FINDINGS_FILE" 2>/dev/null | tr -d ' ')
  total_findings=${total_findings:-0}

  if [ "$total_findings" -gt 0 ]; then
    printf "\n\033[31m✗\033[0m %d historical finding(s) across %d commits.\n" "$total_findings" "$rev_count" >&2
    echo "These exist in past commits and WOULD be exposed if the repo goes public." >&2
    echo "Options:" >&2
    echo "  1. ROTATE the leaked credentials NOW (most common — secret is dead, just clean it up)" >&2
    echo "  2. git filter-repo --invert-paths --path <leak-file>  (rewrite history, force-push)" >&2
    echo "  3. Don't go public until cleaned" >&2
    echo "False positive? Add the value substring to ALLOWED_VALUE_SUBSTRINGS in check-secrets.sh." >&2
    return 1
  fi
  echo "OK — full history clean across $rev_count commits."
  return 0
}

# ──────────────────────────────────────────────────────────────────────
# Mode dispatch

mode="${1:-staged}"
case "$mode" in
  --diff)
    base="${2:?--diff requires <base>}"
    files=$(git -C "$REPO_ROOT" diff --name-only --diff-filter=AM "$base" 2>/dev/null)
    # shellcheck disable=SC2086
    set -- $files
    scan_files "$@"
    ;;
  --files)
    shift
    scan_files "$@"
    ;;
  --history)
    scan_history
    ;;
  --working-tree)
    # Tracked modified + untracked.
    files=$(
      git -C "$REPO_ROOT" diff --name-only --diff-filter=AM 2>/dev/null
      git -C "$REPO_ROOT" ls-files --others --exclude-standard 2>/dev/null
    )
    # shellcheck disable=SC2086
    set -- $files
    scan_files "$@"
    ;;
  --help|-h)
    usage
    ;;
  staged|"")
    # Default — staged files (pre-commit context).
    files=$(git -C "$REPO_ROOT" diff --name-only --cached --diff-filter=AM 2>/dev/null)
    # shellcheck disable=SC2086
    set -- $files
    scan_files "$@"
    ;;
  *)
    echo "unknown mode: $mode" >&2
    usage
    ;;
esac
