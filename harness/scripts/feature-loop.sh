#!/usr/bin/env bash
# feature-loop.sh — unified feature-completeness loop for Sediment.
#
# Each iteration:
#   1. ensure services up + LLM_PROVIDER=claude_cli
#   2. run E2E flows (Playwright) → e2e_pass_rate
#   3. run validator P0+P1+P2 → validator_score
#   4. run UX critic on screenshots → ux_score
#   5. composite = min(e2e_pass_rate, validator_score, ux_score)
#   6. if composite < 9.0:
#        find weakest axis → dispatch sediment-coder (claude -p) → reviewer → ai-commit
#   7. update STATE + JOURNAL
#
# Termination:
#   - 5 consecutive iters with composite >= 9.0  (target reached)
#   - wall time >= MAX_WALL_SECS                  (8 hr default)
#   - MAX_ITERS reached                            (200 default)
#
# Env:
#   MAX_ITERS=200, MAX_WALL_SECS=28800, TARGET_SCORE=9.0
#   ITER_LOG_DIR=output/feature-loop
#   LLM_PROVIDER=claude_cli (forced)

set -uo pipefail

unset CLAUDE_CODE_ENTRYPOINT CLAUDE_CODE_EXECPATH CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS \
      CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS CLAUDE_CODE_MAX_OUTPUT_TOKENS \
      CLAUDE_CODE_SSE_PORT CLAUDECODE \
      OTEL_EXPORTER_OTLP_ENDPOINT OTEL_RESOURCE_ATTRIBUTES OTEL_SERVICE_NAME 2>/dev/null || true

export LLM_PROVIDER=claude_cli
export LLM_MODEL_DEFAULT=claude-sonnet-4-6

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Post-rename (2026-05-15): products/sediment/ → repo root. PROJECT_DIR and
# REPO_ROOT both resolve to the same path now; kept as separate names for
# legacy readers but they're equal.
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROJECT_DIR="$REPO_ROOT"
SVC_DIR="$REPO_ROOT/services/sediment"
VENV="$SVC_DIR/.venv/bin/python"

MAX_ITERS="${MAX_ITERS:-200}"
MAX_WALL_SECS="${MAX_WALL_SECS:-28800}"   # 8 hr
TARGET_SCORE="${TARGET_SCORE:-9.0}"
GREEN_STREAK_TARGET="${GREEN_STREAK_TARGET:-5}"

LOG_DIR="${ITER_LOG_DIR:-$PROJECT_DIR/output/feature-loop}"
mkdir -p "$LOG_DIR"
STATE="$LOG_DIR/STATE.json"
JOURNAL="$LOG_DIR/JOURNAL.md"

START_TS=$(date +%s)

# ── state helpers ─────────────────────────────────────────────────────────────

init_state() {
  if [ ! -f "$STATE" ]; then
    # Fresh state ↔ fresh iter-N-* dirs. Stale dirs cause run_e2e's
    # "skip if dir exists" short-circuit to reuse outdated rubric results
    # — silently scoring 0 for any check that didn't exist in the prior run.
    find "$LOG_DIR" -maxdepth 1 -type d -name "iter-*" -exec rm -rf {} + 2>/dev/null || true
    cat > "$STATE" <<EOF
{
  "iter": 0,
  "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "green_streak": 0,
  "best_composite": 0.0,
  "last_e2e": 0.0,
  "last_validator": 0.0,
  "last_ux": 0.0,
  "last_rag": 0.0,
  "last_concurrent": 0.0,
  "last_composite": 0.0,
  "last_weakest_axis": null,
  "last_dispatch_sha": null,
  "stop_reason": null
}
EOF
  fi
}

state_get()  { $VENV -c "import json;print(json.load(open('$STATE')).get('$1',''))" 2>/dev/null; }
state_set()  {
  # state_set KEY JSON_VALUE
  $VENV -c "
import json,sys
p='$STATE'
d=json.load(open(p))
d['$1']=json.loads('''$2''')
json.dump(d,open(p,'w'),indent=2)
" 2>/dev/null
}

journal() {
  printf "[%s] iter=%s %s\n" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" >> "$JOURNAL"
}

# ── service health ────────────────────────────────────────────────────────────

ensure_services() {
  local all_ok=1
  for port in 3000 10100 10020 11000 12000; do
    local code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 http://localhost:$port/healthz 2>/dev/null || echo "000")
    if [ "$code" = "000" ] || [ "$code" = "404" ]; then
      code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 http://localhost:$port/ 2>/dev/null || echo "000")
    fi
    [ "$code" = "200" ] || all_ok=0
  done
  if [ $all_ok -eq 0 ]; then
    journal "$1" "services-down → bouncing"
    bash "$SCRIPT_DIR/restart-services-if-changed.sh" HEAD~1 2>/dev/null || true
    sleep 8
  fi
}

# ── measurements ──────────────────────────────────────────────────────────────

run_phase_validator() {
  # $1=iter, $2=phase (P0/P1/P2/P3)
  local iter="$1" phase="$2"
  local outdir="$LOG_DIR/iter-$iter-$phase"
  mkdir -p "$outdir"
  (cd "$SVC_DIR" && LLM_PROVIDER=claude_cli "$VENV" -m validator --phase "$phase" --iteration "$iter" --output-dir "$outdir") >"$outdir/run.log" 2>&1 || true
  local latest="$outdir/${phase}-latest.json"
  if [ -f "$latest" ]; then
    $VENV -c "
import json
d=json.load(open('$latest'))
results=d.get('results',[])
if not results: print(0.0); exit()
passed=sum(1 for r in results if r.get('passed'))
print(round(passed/len(results)*10,2))
" 2>/dev/null
  else
    echo "0.0"
  fi
}

run_e2e() {
  # E2E lives inside P2 phase. Subset to results whose layer is L7 (e2e) per rubric.
  local iter="$1"
  local outdir="$LOG_DIR/iter-$iter-P2"
  if [ ! -d "$outdir" ]; then
    run_phase_validator "$iter" "P2" >/dev/null
  fi
  local latest="$outdir/P2-latest.json"
  if [ -f "$latest" ]; then
    $VENV -c "
import json
d=json.load(open('$latest'))
e2e=[r for r in d.get('results',[]) if r.get('id','').startswith('P2-E2E-') or r.get('layer','')=='L7']
if not e2e: print(0.0); exit()
passed=sum(1 for r in e2e if r.get('passed'))
print(round(passed/len(e2e)*10,2))
" 2>/dev/null
  else
    echo "0.0"
  fi
}

run_validator_score() {
  run_phase_validator "$1" "P1"
}

run_ux_score() {
  # Real UX signals — replaces the PNG-count proxy which scored 9.0 even
  # when the actual UI was broken. Composed from 4 components, equally
  # weighted (each 0-10), final score is the average.
  #
  #   mobile      — E2E-10 pass = 10, fail = 0
  #   multi_turn  — E2E-09 pass = 10, fail = 0
  #   no_errors   — 10 - (total console errors across all flow attempts, capped)
  #   screenshots — 10 if >= 40 PNGs, scaled below (preserved as a smoke signal
  #                 that E2E flows actually ran)
  local iter="$1"
  local latest="$LOG_DIR/iter-$iter-P2/P2-latest.json"
  [ -f "$latest" ] || { echo "0.0"; return; }

  $VENV -c "
import json, os, sys
from pathlib import Path

d = json.load(open('$latest'))
results = d.get('results', [])

def axis(check_id):
    for r in results:
        if r.get('id') == check_id:
            return 10.0 if r.get('passed') else 0.0
    return 0.0  # check missing → 0 (not a free pass)

mobile     = axis('P2-E2E-10')
multi_turn = axis('P2-E2E-09')

# Sum console errors across all E2E results — actual['errors'] holds attempt
# failure messages. Real assertion errors come through as strings; we conflate
# them with 'console error budget exceeded' messages for now.
err_count = 0
for r in results:
    if r.get('id','').startswith('P2-E2E-'):
        errs = (r.get('actual') or {}).get('errors') or []
        err_count += len(errs)
no_errors = max(0.0, 10.0 - min(err_count * 2.0, 10.0))

screens_dir = Path('$PROJECT_DIR') / 'output' / 'validation' / 'screenshots'
n_png = 0
if screens_dir.is_dir():
    n_png = sum(1 for _ in screens_dir.rglob('*.png'))
screenshots = min(10.0, n_png / 4.0)  # 40+ PNGs → 10.0; 20 → 5.0

avg = (mobile + multi_turn + no_errors + screenshots) / 4
print(round(avg, 2))
"
}

# Pull recall@3 from P1-GOLDEN-RAG-01 specifically. A single check in the
# validator's 50+-check P1 set, so a regression there gets averaged away in
# the broader `validator` axis. Surfacing it as its own axis ensures the loop
# triggers a fix when recall drops, not when 2-3 unrelated minor checks fail.
run_rag_score() {
  local iter="$1"
  local outdir="$LOG_DIR/iter-$iter-P1"
  local latest="$outdir/P1-latest.json"
  [ -f "$latest" ] || { echo "0.0"; return; }
  $VENV -c "
import json, re
d = json.load(open('$latest'))
for r in d.get('results', []):
    if r.get('id') == 'P1-GOLDEN-RAG-01':
        if r.get('passed'):
            print(10.0); raise SystemExit
        # Pull recall@3 percent from message like 'recall@3=77.5% < 80%'
        m = re.search(r'recall@3=([0-9.]+)%', r.get('message','') or '')
        if m:
            # 100% → 10.0, 80% → 8.0, 0% → 0.0
            print(round(float(m.group(1)) / 10.0, 2)); raise SystemExit
        print(0.0); raise SystemExit
print(10.0)  # check not in this phase → don't penalize
"
}

# Surface P2-CONCUR-01 (concurrent SSE isolation) as its own axis. Same
# reasoning as rag: binary failure of this single check shouldn't be hidden
# in the validator's broader pass-rate.
run_concurrent_score() {
  local iter="$1"
  local outdir="$LOG_DIR/iter-$iter-P2"
  local latest="$outdir/P2-latest.json"
  [ -f "$latest" ] || { echo "0.0"; return; }
  $VENV -c "
import json
d = json.load(open('$latest'))
for r in d.get('results', []):
    if r.get('id') == 'P2-CONCUR-01':
        print(10.0 if r.get('passed') else 0.0); raise SystemExit
print(10.0)
"
}

# ── weakest axis dispatcher ───────────────────────────────────────────────────

find_weakest_axis() {
  # Args: e2e validator ux rag concurrent
  local e2e="$1" val="$2" ux="$3" rag="$4" concur="$5"
  $VENV -c "
scores={'e2e':$e2e,'validator':$val,'ux':$ux,'rag':$rag,'concurrent':$concur}
print(min(scores,key=scores.get))
"
}

failing_check_for_axis() {
  local axis="$1" iter="$2"
  local infile=""
  local target_check=""
  case "$axis" in
    e2e)        infile="$LOG_DIR/iter-$iter-P2/P2-latest.json" ;;
    validator)  infile="$LOG_DIR/iter-$iter-P1/P1-latest.json" ;;
    ux)         infile="$LOG_DIR/iter-$iter-P2/P2-latest.json" ;;
    # rag + concurrent are single-check axes — pre-target the known id so the
    # generic severity-sort can't pick a different failing check by accident.
    rag)        infile="$LOG_DIR/iter-$iter-P1/P1-latest.json"; target_check="P1-GOLDEN-RAG-01" ;;
    concurrent) infile="$LOG_DIR/iter-$iter-P2/P2-latest.json"; target_check="P2-CONCUR-01" ;;
  esac
  [ -f "$infile" ] || { echo ""; return; }
  # If this axis is a single-check axis, return that check's failure directly.
  if [ -n "$target_check" ]; then
    $VENV -c "
import json
d = json.load(open('$infile'))
for r in d.get('results', []):
    if r.get('id') == '$target_check' and not r.get('passed'):
        print('$target_check|' + (r.get('message','') or '')[:200].replace(chr(10),' '))
        break
"
    return
  fi
  # SKIP_CHECKS = comma-separated list of check ids the loop should NOT dispatch fixes for
  # (typically infeasible under claude_cli, like TTFT measured against API latency).
  local skip="${SKIP_CHECKS:-P2-SSE-07}"
  $VENV -c "
import json
skip=set('$skip'.split(','))
d=json.load(open('$infile'))
fails=[r for r in d.get('results',[]) if not r.get('passed') and r.get('id') not in skip]
if not fails: print(''); exit()
fails.sort(key=lambda r: 0 if r.get('severity')=='blocker' else (1 if r.get('severity')=='major' else 2))
r=fails[0]
print(r.get('id','')+'|'+(r.get('message','') or '')[:200].replace(chr(10),' '))
"
}

dispatch_coder() {
  local iter="$1" check_id="$2" msg="$3"
  local prompt_file="$LOG_DIR/iter-$iter-coder-prompt.md"
  cat > "$prompt_file" <<EOF
You are sediment-coder. Read .claude/agents/sediment-coder.md for the contract.

WORK ORDER:
  check_id: $check_id
  message: $msg
  iter: $iter

PROCESS:
1. Read CLAUDE.md
2. Read services/sediment/validator/recipes.yaml — verify $check_id is in ai_propose_review_commit tier
3. Run: bash harness/scripts/ai-commit.sh baseline $check_id P-feature
4. Run: bash harness/scripts/ai-commit.sh begin $check_id
5. Diagnose the failure. Read source. Apply minimal targeted fix via Edit tool.
6. Run: bash harness/scripts/ai-commit.sh gate $check_id P-feature
7. If gate passes: bash harness/scripts/ai-commit.sh commit $check_id "fix($check_id): from feature-loop iter=$iter"
8. If gate fails: bash harness/scripts/ai-commit.sh rollback $check_id

OUTPUT a single-line JSON to stdout at end: {"check_id":"...","verdict":"committed|rolled_back|skipped","sha":"..."}

CONSTRAINTS:
- Forbidden files: init.sql, .env, billing.py, credentials*, .ssh/**, .claude/settings*.json
- Never bypass --no-verify, never edit guard.json
- Stay within services/sediment/ unless the fix is frontend-side (frontend/app/**)
- LLM_PROVIDER is claude_cli; do NOT change that
EOF

  cd "$REPO_ROOT"
  local log="$LOG_DIR/iter-$iter-coder.log"
  journal "$iter" "dispatch coder ($check_id)"
  # Use claude -p in headless mode with sonnet for speed
  claude -p --dangerously-skip-permissions --model sonnet \
    --output-format json \
    "$(cat "$prompt_file")" > "$log" 2>&1 || true

  # Extract verdict from JSON output
  local verdict=$($VENV -c "
import json,re,sys
try:
    d=json.load(open('$log'))
    result=d.get('result','')
except: result=''
m=re.search(r'\{\"check_id\":[^}]+\}',result)
print(m.group(0) if m else '{\"verdict\":\"unknown\"}')
" 2>/dev/null)
  echo "$verdict"
}

# ── main loop ─────────────────────────────────────────────────────────────────

init_state
journal "init" "feature-loop start TARGET=$TARGET_SCORE MAX_ITERS=$MAX_ITERS WALL=${MAX_WALL_SECS}s"

iter=$(state_get iter)
[ -z "$iter" ] && iter=0

while true; do
  iter=$((iter+1))
  state_set iter "$iter"

  # exit conditions
  elapsed_s=$(($(date +%s) - START_TS))
  if [ "$iter" -gt "$MAX_ITERS" ]; then
    state_set stop_reason '"max_iters"'
    journal "$iter" "STOP max_iters"
    break
  fi
  if [ "$elapsed_s" -ge "$MAX_WALL_SECS" ]; then
    state_set stop_reason '"wall_time"'
    journal "$iter" "STOP wall_time ${elapsed_s}s >= ${MAX_WALL_SECS}s"
    break
  fi

  journal "$iter" "── BEGIN ──"
  ensure_services "$iter"

  e2e=$(run_e2e "$iter")
  val=$(run_validator_score "$iter")
  # ux_score now derives from real signals (mobile + multi-turn + console
  # errors + screenshot smoke) instead of just PNG count.
  ux=$(run_ux_score "$iter")
  rag=$(run_rag_score "$iter")
  concur=$(run_concurrent_score "$iter")
  composite=$($VENV -c "print(round(min($e2e,$val,$ux,$rag,$concur),2))")

  state_set last_e2e "$e2e"
  state_set last_validator "$val"
  state_set last_ux "$ux"
  state_set last_rag "$rag"
  state_set last_concurrent "$concur"
  state_set last_composite "$composite"

  journal "$iter" "e2e=$e2e val=$val ux=$ux rag=$rag concur=$concur composite=$composite"

  if $VENV -c "import sys;sys.exit(0 if $composite>=$TARGET_SCORE else 1)"; then
    streak=$(state_get green_streak)
    streak=$((streak+1))
    state_set green_streak "$streak"
    journal "$iter" "GREEN streak=$streak/$GREEN_STREAK_TARGET"
    if [ "$streak" -ge "$GREEN_STREAK_TARGET" ]; then
      state_set stop_reason '"target_reached"'
      journal "$iter" "STOP target_reached composite>=$TARGET_SCORE x $streak"
      break
    fi
    sleep 30  # cool-down before re-confirm
    continue
  fi

  state_set green_streak 0

  # need a fix
  axis=$(find_weakest_axis "$e2e" "$val" "$ux" "$rag" "$concur")
  state_set last_weakest_axis "\"$axis\""
  journal "$iter" "weakest=$axis"

  failing=$(failing_check_for_axis "$axis" "$iter")
  if [ -z "$failing" ]; then
    journal "$iter" "no failing check resolvable; sleeping"
    sleep 60
    continue
  fi

  check_id="${failing%%|*}"
  msg="${failing#*|}"
  journal "$iter" "target_check=$check_id"

  verdict=$(dispatch_coder "$iter" "$check_id" "$msg")
  journal "$iter" "coder=$verdict"

  # brief cooldown so next iter sees fresh services
  sleep 15
done

journal "end" "feature-loop terminated reason=$(state_get stop_reason)"
echo "feature-loop done. STATE=$STATE"
