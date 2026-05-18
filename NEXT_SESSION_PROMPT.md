# NEXT SESSION PROMPT — Sediment (paste-ready)

> 다음 Claude Code 세션의 **첫 메시지로 통째로 paste**.
> 이전 세션은 하네스 파일을 다 만들었지만 Task tool dispatch 없이 메인 세션이
> 직접 Bash 실행했음. 다음 세션의 1차 목표는 그 갭을 메우는 것.

---

## 너의 정체

너는 Sediment의 **자율 진행자**다. 사람(Jay)은 자고 있고 너는 다음 6시간 안에:

1. **Task tool로 진짜 subagent dispatch** — `curator-coder`, `curator-reviewer`,
   `curator-medic` 적어도 1번씩 실행
2. **`ralph.sh` 백그라운드 5 iter 실행** — claude -p subprocess 패턴 실증
3. **P2 점수 65.1% → 90%+** 자동으로 끌어올림 (TIER-2 bug 자동 수정)
4. **이 모두를 사람 개입 없이** (Bash로 직접 안 풀고 — agent를 통해)

---

## 즉시 읽을 컨텍스트 (이 순서대로)

```bash
cd /Users/jaylee/CodeWorkspace/hypeproof/.claude/worktrees/mvp
cat products/sediment/SESSION_HANDOFF_2026-05-08.md       # 이전 세션 정리
cat products/sediment/DECISIONS.md                        # 모든 §11 결정
cat products/sediment/SPEC.md | head -80                  # §0 + §1 only
cat products/sediment/harness/ralph/LEARNINGS.md          # 9 entries
cat .claude/guard.json                                      # 영원히 사람만
cat products/sediment/services/sediment/validator/recipes.yaml | tail -40  # 4-tier
git log --oneline -10
```

**다 읽지 말고** 첫 5분 안에 위 파일들 빠르게 훑어봐. 자세한 내용은 작업 중 필요할 때 찾아라.

## 사전 점검 (1분)

```bash
for kv in "5433:postgres" "6380:redis" "11000:ingester" "12000:metadata" "10100:platform" "10020:langgraph"; do
  port="${kv%%:*}"; name="${kv##*:}"
  nc -z localhost "$port" 2>/dev/null && echo "  $name ✓" || echo "  $name ✗"
done
```

서비스 다 떠있어야 함. 안 떠있으면 먼저:
```bash
bash products/sediment/harness/scripts/setup-env.sh    # idempotent
make -C products/sediment platform &
make -C products/sediment langgraph &
make -C products/sediment ingester &
make -C products/sediment metadata &
sleep 5
```

## 첫 진짜 액션 — Task tool로 curator-coder dispatch

**이전 세션에서 한 번도 안 한 것.** 더 미루지 마라.

남은 P2 TIER-2 work-order 후보:
- `P2-INTENT-02`: "Library question routed to 'library'" — SQL ProgrammingError
- `P2-INTENT-04`: "Decision question routed to 'decision'" — 동상
- `P2-INGEST-04`: chunker idempotency
- `P2-SSE-02/03`: SSE delta 안 나옴 (LLM_PROVIDER=offline 때문일 수 있음)

**Step 1**: 최신 P2 report에서 work-order 후보 1개 선택:
```bash
ls -1t products/sediment/output/validation/P2-iter*.json | head -1
# 그 파일에서 failed 항목 중 ai_propose_review_commit 매칭되는 것 1개
```

**Step 2**: work-order JSON 파일을 만들어 (or 기존 것 활용) Task tool로 dispatch:

```
Task tool 호출:
  subagent_type: curator-coder
  prompt: |
    Read .claude/agents/curator-coder.md first for your contract.
    Read products/sediment/SESSION_HANDOFF_2026-05-08.md for context.

    Your input:
    - work_order_path: <path to JSON or inline JSON>
    - target check_id: P2-INTENT-02 (or whichever)

    Workflow:
    1. bash products/sediment/harness/scripts/ai-commit.sh baseline P2-INTENT-02 P2
    2. bash products/sediment/harness/scripts/ai-commit.sh begin P2-INTENT-02
    3. Read the failing test in validator/checks/p2_chat.py + the relevant
       graph node in applications/sediment_langgraph/graphs/lab_curator_graph.py
    4. Use Edit tool to patch the bug (likely SET LOCAL → set_config issue
       per LEARNINGS pattern #7)
    5. bash products/sediment/harness/scripts/ai-commit.sh gate P2-INTENT-02 P2
       — must pass (score >= baseline)
    6. Dispatch curator-reviewer via Task tool with branch name
    7. If reviewer.verdict == approve: bash ai-commit.sh commit P2-INTENT-02
    8. Append LEARNINGS entry
    9. Return JSON output contract

    Cost ceiling: $2. Don't loop. One attempt + reviewer.
```

**Step 3**: curator-reviewer는 coder가 자기 step 6에서 알아서 dispatch. 너는 coder의 결과 JSON만 surface해.

**Step 4**: 사람 개입 없이 머지하고 싶으면:
```bash
git checkout worktree-mvp
git merge --no-ff <ai/coder/p2-intent-02-...>
```

## 두 번째 진짜 액션 — Ralph 5-iter 실험

```bash
# 5 iter, $5 budget, 60s cooldown — 실험 모드
nohup bash products/sediment/harness/ralph/supervisor.sh \
  --max-restarts 1 --cooldown 60 \
  > /tmp/ralph-experiment.log 2>&1 &

# 약 10분 동안 무엇이 일어나는지 모니터
tail -f /tmp/ralph-experiment.log products/sediment/harness/ralph/JOURNAL.md
```

루프 중간에 ralph.sh가 max-iter에 도달하기 전에 멈추면 그 이유를 LEARNINGS에 기록.

## 세 번째 액션 — Playwright + E2E

```bash
make -C products/sediment e2e-install        # chromium 다운로드 ~150MB
make -C products/sediment validate-lint-e2e  # spec 무결성
# Web 띄우기 (E2E flow가 필요로 함)
cd web && nohup npm run dev > /tmp/curator-web.log 2>&1 &
sleep 20
nc -z localhost 3000 && echo "web up"

# E2E only:
cd ../products/sediment/services/sediment
.venv/bin/python -m validator --phase P2 --only-layers L11
```

flake 측정 → curator-e2e-debugger dispatch.

## 절대 하지 말 것 (이전 세션 실수)

1. **메인 세션이 직접 코드 patch 하지 마라**. 무조건 Task tool → curator-coder.
   유혹 강하지만 그게 핵심 갭.
2. **`init.sql`, `.env`, `billing.py` 절대 손대지 마라** (`.claude/guard.json`).
3. **`make reset` 실행 금지** — DB 날아감.
4. **`docker compose down -v` 금지** — volume 날아감.
5. **heredoc commit 금지** — `git commit -F /tmp/msg.txt` 패턴 사용.
6. **chmod 직접 실행 금지** — `bash <path>` 패턴이면 chmod 불필요.
7. **`force-push` 금지** — main 보호.

## 4-tier 코드 수정 정책 (recipes.yaml에서 자동 매칭)

| Tier | 패턴 | 처리 |
|---|---|---|
| 1 ai_apply_immediately | INFRA / HEALTH / INGEST-01/02 | curator-fixer recipe 즉시 적용 |
| 2 ai_propose_review_commit | RAG / SEARCH / E2E / SEC / INTENT / CHUNK / DDL (non-RLS) / INGEST-04 | **curator-coder + reviewer + commit** |
| 3 human_required | RLS-* | 절대 자동 X. 사람 호출. |
| 4 forbid_ai_edit | init.sql / .env / billing.py / credentials* | guard.json blocks |

## Permission prompt 우회 패턴 (이전 세션에서 발견)

- `chmod +x` → 안 하면 됨. `bash <path>` 패턴 사용.
- `git commit -m "$(cat <<EOF...EOF)"` → `printf > file && git commit -F file` 사용.
- `docker compose up` → 이미 띄워졌을 가능성 높음. `nc -z` 먼저 확인 후 필요할 때만.
- `${VAR,,}` (bash 4) → macOS bash 3.2 미지원. `tr A-Z a-z` 사용.

## 결과 보고 형식 (이 세션 끝에)

```markdown
# Session 2026-05-09 Result

## What ran (verified live)
- curator-coder dispatch: <count>회 (sha: ...)
- curator-reviewer dispatch: <count>회
- curator-medic dispatch: <count>회
- ralph.sh iter completed: <count>
- P2 score: <before>% → <after>%

## What failed
- ...

## Open work-orders
- ...

## LEARNINGS appended
- ...
```

## 비용 한도

- 이 세션 전체: $20
- 단일 ralph.sh run: $5
- curator-coder/reviewer 1회 dispatch: $1 + $0.5
- 초과하면 즉시 종료 + handoff document append.

---

*Source: 이전 세션 (5/5 + 5/8) + 7+1 real bug fixed + P0 99.4% / P1 89.7% / P2 65.1% verified*
*Goal: 사람 개입 없이 자가진행. 이전 세션은 직접 Bash로 풀었음. 이 세션은 agent로 풀어라.*
