#!/usr/bin/env bash
# Measures wall time of 50 parallel `sediment search` subprocesses on one host.
# Pass criterion (per cli-test-requirements.md §5.6):
#   median wall time per invocation ≤ 50ms (Rust static binary fork+exec+init)
#
# Usage:
#   BASE_URL=http://localhost:10101 JWT=... bash cli_fork_cost.sh

set -euo pipefail

: "${BASE_URL:?need BASE_URL}"
: "${JWT:?need JWT}"

SEDIMENT_BIN="${SEDIMENT_BIN:-$(dirname "$0")/../target/release/sediment}"
if [[ ! -x "$SEDIMENT_BIN" ]]; then
  echo "Build the release binary first: cargo build --release" >&2
  exit 1
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

run_one() {
  local i=$1
  local start
  start=$(python3 -c 'import time; print(time.monotonic_ns())')
  SEDIMENT_BASE_URL="$BASE_URL" SEDIMENT_TOKEN="$JWT" \
    "$SEDIMENT_BIN" --format json search "research" --limit 1 > /dev/null 2>&1
  local end
  end=$(python3 -c 'import time; print(time.monotonic_ns())')
  echo $(( (end - start) / 1000000 )) > "$TMP/$i"
}

echo "spawning 50 parallel sediment search subprocesses..."
for i in $(seq 1 50); do
  run_one "$i" &
done
wait

# Aggregate
times=$(cat "$TMP"/* | sort -n)
count=$(echo "$times" | wc -l | tr -d ' ')
p50=$(echo "$times" | awk -v c="$count" 'NR==int(c/2){print}')
p95=$(echo "$times" | awk -v c="$count" 'NR==int(c*0.95){print}')

echo "count=$count  p50=${p50}ms  p95=${p95}ms"
if (( p50 > 50 )); then
  echo "FAIL: p50 ${p50}ms > 50ms target"
  exit 1
fi
echo "PASS"
