#!/usr/bin/env bash
# fly-exec.sh — drop-in replacement for `fly ssh console -C "..."` that doesn't hang.
#
# Why this exists
# ---------------
# `fly ssh console -C "<cmd>"` keeps the client process alive indefinitely
# after the remote command exits — even when stdin is closed and the command
# would return locally in < 1 s. We hit this on 2026-05-22 with two stuck
# reembed sessions still holding ESTABLISHED TCP after 6+ hours. flyctl 0.4.53
# does not propagate the remote process's exit through the SSH session reliably
# when invoked non-interactively (e.g. backgrounded shells, claude tool calls,
# CI). See sediment#54.
#
# The fix is the newer `fly machine exec <machine-id> "<cmd>"` API. It returns
# cleanly in ~1 s for the same workload, honors a built-in --timeout, and does
# not need a PTY.
#
# Usage
# -----
#   bash harness/scripts/fly-exec.sh "<command>"
#   bash harness/scripts/fly-exec.sh --timeout 60 "<command>"
#   bash harness/scripts/fly-exec.sh --app other-app "<command>"
#
# Defaults:
#   --app      hypeproof-sediment
#   --timeout  120  (seconds; fly machine exec's built-in)
#
# Exit code is the remote command's exit code, or 124 on timeout.

set -euo pipefail

APP="${SEDIMENT_FLY_APP:-hypeproof-sediment}"
TIMEOUT="${SEDIMENT_FLY_EXEC_TIMEOUT:-120}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --app)     APP="$2"; shift 2 ;;
        --timeout) TIMEOUT="$2"; shift 2 ;;
        --help|-h)
            sed -n '2,30p' "$0"
            exit 0
            ;;
        --) shift; break ;;
        -*) echo "fly-exec: unknown flag $1" >&2; exit 2 ;;
        *)  break ;;
    esac
done

if [[ $# -lt 1 ]]; then
    echo "fly-exec: missing command. Usage: fly-exec.sh [--app A] [--timeout N] \"<cmd>\"" >&2
    exit 2
fi
CMD="$*"

# Pick the first started machine for this app. We assume single-machine apps
# (current Sediment topology). For multi-machine apps, set SEDIMENT_FLY_MACHINE
# to override.
if [[ -n "${SEDIMENT_FLY_MACHINE:-}" ]]; then
    MID="$SEDIMENT_FLY_MACHINE"
else
    MID="$(fly machines list -a "$APP" --json 2>/dev/null \
        | python3 -c 'import json,sys
ms = json.load(sys.stdin)
for m in ms:
    if m.get("state") == "started":
        print(m["id"]); break' 2>/dev/null || true)"
fi

if [[ -z "$MID" ]]; then
    echo "fly-exec: no started machine found for app=$APP" >&2
    exit 3
fi

# `fly machine exec` invokes the command via execve directly (no remote shell),
# unlike `fly ssh console -C` which goes through a login shell. To preserve the
# old behavior (pipes, &&, env-var expansion happen on the remote), wrap in
# `sh -c "..."`. CMD is escaped for double-quote context: \, ", $, ` get
# backslash-escaped. Avoid this layer if you need raw single quotes — pass a
# script path instead (e.g. `bash /tmp/your_script.sh`).
ESCAPED="$CMD"
ESCAPED="${ESCAPED//\\/\\\\}"   # \ → \\
ESCAPED="${ESCAPED//\"/\\\"}"   # " → \"
ESCAPED="${ESCAPED//\$/\\\$}"   # $ → \$
ESCAPED="${ESCAPED//\`/\\\`}"   # ` → \`
exec fly machine exec "$MID" "sh -c \"$ESCAPED\"" -a "$APP" --timeout "$TIMEOUT"
