#!/usr/bin/env bash
# smoke-tests.sh — fast Python smoke tests for the safety-critical code we keep touching.
#
# These are NOT a replacement for the full pytest suite. They're the
# tiny canaries that catch the LEARNINGS-class drifts: 4-tier policy
# breaking, JWT roundtrip failing, settings guards regressing, search
# helpers diverging, chunker re-introducing the KO multibyte / overlap
# bugs. Each block is ~10 LOC and runs in <1s.
#
# Run after any change to:
#   lab_lib/{auth,settings,audit,search_utils,chunker}.py
#   validator/{fixer,dispatch}.py + recipes.yaml + rubric.yaml
#   applications/sediment_platform/routers/{onboarding,members,billing,conversations}.py
#   lab_lib/connectors/{discord,github_repo}.py
#
# Exit codes: 0 = all pass, 1 = at least one fail.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SVC="$REPO_ROOT/services/sediment"
PY="$SVC/.venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "FAIL: $SVC/.venv missing — run: make install" >&2
  exit 1
fi

# Each test runs in its own subprocess so a sys.exit/assert/ModuleError in
# one doesn't kill the rest. Captured stdout is shown; status is the exit code.
PASS=0
FAIL=0
FAIL_NAMES=()

run() {
  local name="$1"; shift
  local code="$1"
  if ( cd "$SVC" && PYTHONPATH=. "$PY" -c "$code" ) >/tmp/smoke-$$.log 2>&1; then
    printf "  \033[32m✓\033[0m %s\n" "$name"
    PASS=$((PASS + 1))
  else
    printf "  \033[31m✗\033[0m %s\n" "$name"
    sed 's/^/      /' /tmp/smoke-$$.log
    FAIL=$((FAIL + 1))
    FAIL_NAMES+=("$name")
  fi
  rm -f /tmp/smoke-$$.log
}

# ─── lab_lib.auth — JWT mint/decode + service token ────────────────────
run "auth: JWT mint+decode roundtrip (human admin)" '
import sys; sys.path.insert(0, ".")
from lab_lib.auth import mint_token, decode_token
t = mint_token("00000000-0000-0000-0000-000000000999", "tenant-a", role="admin", email="x@y.z")
i = decode_token(t)
assert i.member_id.endswith("000999") and i.role == "admin" and i.tenant_id == "tenant-a"
assert not i.is_service, "human token should not be service"
'
run "auth: service JWT (mint_service_token + is_service)" '
import sys; sys.path.insert(0, ".")
from lab_lib.auth import mint_service_token, decode_token
t = mint_service_token(caller="cron.daily_ingest", tenant_id="tenant-a")
i = decode_token(t)
assert i.is_service, f"expected service, got role={i.role!r}"
assert i.member_id == "service:cron.daily_ingest"
'
run "auth: mint_token refuses role=service (must use mint_service_token)" '
import sys; sys.path.insert(0, ".")
from lab_lib.auth import mint_token
try:
    mint_token("m1", "t1", role="service")
    raise AssertionError("expected ValueError")
except ValueError:
    pass
'

# ─── lab_lib.settings — prod boot guard ────────────────────────────────
run "settings: prod guard SystemExit on default jwt_secret" '
import sys, os
os.environ["SEDIMENT_ENV"] = "prod"
os.environ.pop("JWT_SECRET", None)
sys.path.insert(0, ".")
from lab_lib.settings import validate_runtime_secrets
try:
    validate_runtime_secrets()
    raise AssertionError("expected SystemExit")
except SystemExit as e:
    assert e.code == 2, f"expected exit 2, got {e.code}"
'

# ─── validator.fixer — 4-tier policy enforced ──────────────────────────
run "fixer: 4-tier policy aggregates 3 tier blocks (not just legacy alias)" '
import sys, yaml; sys.path.insert(0, ".")
from validator.fixer import _collect_no_auto_patterns
doc = yaml.safe_load(open("validator/recipes.yaml"))
pats = _collect_no_auto_patterns(doc)
required = {"P*-RLS-*", "infra/init.sql", ".env",
            "services/sediment/applications/sediment_platform/routers/billing.py",
            "**/credentials*"}
missing = required - set(pats)
assert not missing, f"fixer missing protected patterns: {missing}"
'

# ─── lab_lib.search_utils — drift-resistant retrieval helpers ──────────
run "search_utils: stop-words filter strips English noise" '
import sys; sys.path.insert(0, ".")
from lab_lib.search_utils import build_ts_or_query
assert build_ts_or_query("what is the spec about") == "spec"
'
run "search_utils: Korean preserved (no stop-word filter on KO)" '
import sys; sys.path.insert(0, ".")
from lab_lib.search_utils import build_ts_or_query
q = build_ts_or_query("큐레이터 활동")
assert "큐레이터" in q and "활동" in q
'
run "aliases: detect_type token-match (NOT substring) — '"'"'칼럼이나'"'"' must NOT match column" '
import sys; sys.path.insert(0, ".")
from lab_lib.aliases import build_index, EMPTY_INDEX
# sediment#139: this vocabulary moved out of search_utils into the
# tenant_aliases table, so the index is built from data here rather than
# imported. The invariant is unchanged: matching is token-level, so
# "칼럼이나" (= column-or) must NOT fire the column boost.
idx = build_index([("칼럼", "type", "column")])
assert idx.detect_type("보안 칼럼") == "column"
assert idx.detect_type("칼럼이나 제안") is None
# A tenant with no configured vocabulary gets no boosts at all — not another
# tenant proper nouns, which is what the old hardcoded map handed it.
assert EMPTY_INDEX.detect_type("보안 칼럼") is None
'
run "search_utils: is_zero_vector detects offline-mode zero vec" '
import sys; sys.path.insert(0, ".")
from lab_lib.search_utils import is_zero_vector
assert is_zero_vector([0.0] * 1536) is True
assert is_zero_vector([0.001] + [0.0] * 1535) is False
'

# ─── lab_lib.chunker — heading_path + Korean multibyte ─────────────────
run "chunker: section-crossing overlap suppressed (no A leak into B)" '
import sys; sys.path.insert(0, ".")
from lab_lib.chunker import chunk_markdown
md = "# Section A\n\n" + ("A content. " * 400) + "\n\n# Section B\n\n" + ("B content. " * 400)
chunks = chunk_markdown(md, max_tokens=300, overlap_tokens=50)
b = [c for c in chunks if "Section B" in c.heading_path]
assert b, "no Section B chunks"
assert "A content" not in b[0].content, "section A leaked into B"
'
run "chunker: Korean overlap chunks do NOT start with U+FFFD" '
import sys; sys.path.insert(0, ".")
from lab_lib.chunker import chunk_markdown
md = "# 한글\n\n" + "다국어 문서의 조각 경계에서 자모가 깨지지 않는지 확인합니다. " * 80
chunks = chunk_markdown(md, max_tokens=200, overlap_tokens=30)
assert len(chunks) >= 2
for c in chunks[1:]:
    assert not c.content.startswith("�"), f"� prefix: {c.content[:30]!r}"
'

# ─── lab_lib.connectors.github_repo — prod PAT mandate ─────────────────
run "github connector: prod env without token raises RuntimeError" '
import os, sys
os.environ["SEDIMENT_ENV"] = "prod"
for k in ("GITHUB_TOKEN", "GH_TOKEN", "HYPEPROOF_GITHUB_TOKEN", "SEDIMENT_GITHUB_ALLOW_ANONYMOUS"):
    os.environ.pop(k, None)
sys.path.insert(0, ".")
from lab_lib.connectors.github_repo import GitHubRepoConnector
try:
    GitHubRepoConnector(repos=["foo/bar"])
    raise AssertionError("expected RuntimeError")
except RuntimeError as e:
    assert "GITHUB_TOKEN" in str(e)
'

# ─── lab_lib.connectors.discord — circuit breaker primitives ───────────
run "discord: circuit breaker state machine works" '
import sys; sys.path.insert(0, ".")
from lab_lib.connectors.discord import _breaker_open, _breaker_record_failure, _breaker_record_success
ch = "test-channel-id"
assert not _breaker_open(ch)
for _ in range(3):
    _breaker_record_failure(ch)
assert _breaker_open(ch), "breaker should trip after 3 failures"
'

# ─── conversations.py — bound ILIKE (no f-string interp) ───────────────
run "conversations: _exclude_test_sql returns (fragment, params) — bound" '
import sys; sys.path.insert(0, ".")
from applications.sediment_platform.routers.conversations import _exclude_test_sql
frag, params = _exclude_test_sql()
assert "ILIKE ANY" in frag and "test_title_patterns" in params
assert all(p.endswith("%") for p in params["test_title_patterns"])
'

# ─── billing.py — Stripe webhook structure ─────────────────────────────
run "billing: webhook endpoint imports cleanly with stripe SDK" '
import sys; sys.path.insert(0, ".")
from applications.sediment_platform.routers.billing import stripe_webhook
import stripe
assert hasattr(stripe, "Webhook"), "stripe SDK loaded"
'

# ──────────────────────────────────────────────────────────────────────
echo
total=$((PASS + FAIL))
if [ "$FAIL" -eq 0 ]; then
  printf "  \033[32m✓ %d/%d smoke tests passed\033[0m\n" "$PASS" "$total"
  exit 0
fi
printf "  \033[31m✗ %d/%d smoke tests failed:\033[0m\n" "$FAIL" "$total"
printf "    - %s\n" "${FAIL_NAMES[@]}"
exit 1
