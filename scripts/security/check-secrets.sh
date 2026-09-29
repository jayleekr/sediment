#!/usr/bin/env bash
# check-secrets.sh - high-confidence secret scanner for HypeProof repos.
#
# Default mode scans staged files. CI should use `--diff <base>`.
# Output intentionally does not print matched secret values.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# All repo enumeration goes through this handle. Two things are pinned so the
# scan cannot depend on the ambient git config or the OS default:
#
#   core.quotePath=false  git otherwise renders a non-ASCII path as octal
#                         escapes wrapped in double quotes ("한글.txt" ->
#                         "\355\225\234.txt"). On a stock CI runner quotePath
#                         defaults to TRUE, so a secret in a Korean-named file
#                         was word-split into a path that did not exist and was
#                         silently skipped while the job stayed green. It only
#                         worked on a workstation whose *global* git config
#                         happened to set quotePath=false.
#   -z (at each call)     NUL-delimited output so a path bearing spaces or
#                         newlines survives enumeration intact, instead of being
#                         split on IFS by the old `set -- $files`.
#
# See tests/security/test_check_secrets.py::test_non_ascii_* for the control.
GIT=(git -C "$REPO_ROOT" -c core.quotePath=false)

DANGEROUS_PATHS=(
  '.env'
  '.env.local'
  '.env.production'
  '.env.development'
  'credentials.json'
  'service-account*.json'
  '*.pem'
  '*.key'
  '*.p12'
  '*.pfx'
  '.npmrc'
  '.netrc'
)

EXCLUDED_PATHS=(
  '.gitignore'
  'scripts/security/check-secrets.sh'
  'tests/security/test_check_secrets.py'
)

SECRET_PATTERNS=(
  'Google OAuth client secret|GOCSPX-[A-Za-z0-9_-]{20,}'
  'Google API key|AIza[0-9A-Za-z_-]{35}'
  'PEM private key|-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'
  'GitHub classic token|ghp_[0-9A-Za-z]{36}'
  'GitHub fine-grained token|github_pat_[0-9A-Za-z_]{60,}'
  'OpenAI API key|sk-[A-Za-z0-9]{40,}'
  'OpenAI project key|sk-proj-[A-Za-z0-9_-]{40,}'
  'Anthropic API key|sk-ant-(api03|admin01)-[A-Za-z0-9_-]{80,}'
  'Slack bot token|xoxb-[0-9]{10,}-[0-9]{10,}-[0-9A-Za-z]{24,}'
  'Discord webhook URL|discord(app)?\.com/api/webhooks/[0-9]+/[A-Za-z0-9_-]{30,}'
)

FINDINGS_FILE="$(mktemp)"
trap 'rm -f "$FINDINGS_FILE"' EXIT

usage() {
  cat >&2 <<'EOF'
Usage:
  scripts/security/check-secrets.sh
  scripts/security/check-secrets.sh --diff <base>
  scripts/security/check-secrets.sh --files <paths...>
  scripts/security/check-secrets.sh --working-tree
EOF
  exit 2
}

path_matches_glob() {
  local path="$1"
  local glob="$2"
  # shellcheck disable=SC2254
  case "$path" in $glob) return 0 ;; *) return 1 ;; esac
}

is_excluded() {
  local path="$1"
  local base
  base="$(basename "$path")"
  for glob in "${EXCLUDED_PATHS[@]}"; do
    if path_matches_glob "$path" "$glob" || path_matches_glob "$base" "$glob"; then
      return 0
    fi
  done
  return 1
}

is_dangerous_path() {
  local path="$1"
  local base
  base="$(basename "$path")"
  for glob in "${DANGEROUS_PATHS[@]}"; do
    if path_matches_glob "$path" "$glob" || path_matches_glob "$base" "$glob"; then
      return 0
    fi
  done
  return 1
}

resolve_file() {
  local path="$1"
  if [ -f "$path" ]; then
    printf '%s' "$path"
  elif [ -f "$REPO_ROOT/$path" ]; then
    printf '%s' "$REPO_ROOT/$path"
  fi
}

is_binary_or_large() {
  local path="$1"
  case "$path" in
    *.png|*.jpg|*.jpeg|*.gif|*.webp|*.ico|*.pdf|*.zip|*.gz|*.tgz|*.xz|*.dmg|*.mp4|*.mov|*.woff|*.woff2)
      return 0
      ;;
  esac
  local size
  size=$(stat -f%z "$path" 2>/dev/null || stat -c%s "$path" 2>/dev/null || echo 0)
  [ "$size" -gt 1048576 ] && return 0
  local encoding
  encoding=$(file -b --mime-encoding "$path" 2>/dev/null || echo unknown)
  [ "$encoding" = "binary" ]
}

record_finding() {
  local path="$1"
  local label="$2"
  printf '  \033[31mFAIL\033[0m %s - %s\n' "$path" "$label" >&2
  echo 1 >> "$FINDINGS_FILE"
}

scan_file() {
  local path="$1"
  [ -z "$path" ] && return 0
  is_excluded "$path" && return 0

  if is_dangerous_path "$path"; then
    record_finding "$path" "dangerous credential path"
    return 0
  fi

  # 심볼릭 링크 자체는 경로 문자열일 뿐 비밀을 담을 수 없다. 링크가 디렉터리를
  # 가리키면 -f가 거짓이라 아래 fail-closed 분기에 걸리는데, 그건 누락이 아니라
  # 링크다 — 스킬을 등록할 때마다(.claude/skills/<name>) 오탐이 난다.
  # 대상 파일이 추적된다면 그 파일 자체로 따로 스캔된다.
  if [ -L "$path" ] || [ -L "$REPO_ROOT/$path" ]; then
    return 0
  fi

  local file
  file="$(resolve_file "$path")"
  if [ -z "$file" ]; then
    # A path named by git enumeration but absent on disk was NOT inspected.
    # Under the strict (enumeration) modes that is fail-closed, never a silent
    # skip — it is the exact shape of the quotePath defect this guard was
    # hardened against. In --files mode the caller owns the path list, so a
    # missing path stays a no-op for backward compatibility.
    if [ "${SCAN_STRICT_MISSING:-0}" = "1" ]; then
      record_finding "$path" "named by git but absent on disk (fail-closed)"
    fi
    return 0
  fi
  is_binary_or_large "$file" && return 0

  local content
  content="$(cat "$file" 2>/dev/null)"
  for entry in "${SECRET_PATTERNS[@]}"; do
    local label="${entry%%|*}"
    local regex="${entry#*|}"
    if printf '%s' "$content" | grep -E -q -e "$regex" 2>/dev/null; then
      record_finding "$path" "$label"
    fi
  done
}

scan_files() {
  local total=0
  for path in "$@"; do
    total=$((total + 1))
    scan_file "$path"
  done

  local findings
  findings="$(wc -l < "$FINDINGS_FILE" 2>/dev/null | tr -d ' ')"
  findings="${findings:-0}"
  if [ "$findings" -gt 0 ]; then
    printf '\033[31mFAIL\033[0m %s secret finding(s) across %s candidate file(s).\n' "$findings" "$total" >&2
    return 1
  fi
  echo "OK - scanned $total candidate file(s), no secrets found."
  return 0
}

# Read a NUL-delimited stream into the global FILES array. Hand-rolled because
# bash 3.2 (the macOS system bash this also runs under) has no `mapfile`.
read_nul_into_files() {
  FILES=()
  local item
  while IFS= read -r -d '' item; do
    FILES+=("$item")
  done
}

# Control invariant: announce what this scan pinned so a reader of any CI log —
# on any platform — can see the scan did not silently inherit the ambient git
# config or OS default. This is the observable half of the hermeticity fix.
report_invariant() {
  printf 'check-secrets: platform=%s enumeration=NUL core.quotePath=pinned-false mode=%s\n' \
    "$(uname -s)" "$1" >&2
}

mode="${1:-staged}"
case "$mode" in
  staged)
    report_invariant staged
    SCAN_STRICT_MISSING=1
    read_nul_into_files < <("${GIT[@]}" diff --cached -z --name-only --diff-filter=AM 2>/dev/null)
    scan_files ${FILES[@]+"${FILES[@]}"}
    ;;
  --diff)
    [ $# -eq 2 ] || usage
    report_invariant "--diff"
    SCAN_STRICT_MISSING=1
    read_nul_into_files < <("${GIT[@]}" diff -z --name-only --diff-filter=AM "$2" 2>/dev/null)
    scan_files ${FILES[@]+"${FILES[@]}"}
    ;;
  --files)
    shift
    scan_files "$@"
    ;;
  --working-tree)
    report_invariant "--working-tree"
    SCAN_STRICT_MISSING=1
    read_nul_into_files < <(
      "${GIT[@]}" diff -z --name-only --diff-filter=AM 2>/dev/null
      "${GIT[@]}" ls-files -z --others --exclude-standard 2>/dev/null
    )
    scan_files ${FILES[@]+"${FILES[@]}"}
    ;;
  --help|-h)
    usage
    ;;
  *)
    usage
    ;;
esac
