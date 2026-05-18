# Harness Workflow (개발 / 테스트 / 검증 / 루프)

> 이전 세션은 이 워크플로의 파일을 다 만들었지만 **메인 Claude Code 세션이
> 직접 Bash로 실행**했음. 다음 세션은 **Task tool → subagent dispatch** 방식으로
> 진짜 자동화를 돌릴 것.

---

## 4단계 워크플로 — 누가 무엇을 하나

```
┌─────────────────────────────────────────────────────────────────────────┐
│ 1. DEVELOP (코드 변경 — TIER-2 work-order 처리)                         │
│                                                                         │
│   메인 세션                                                             │
│     ├─ Task tool: subagent_type=curator-coder                          │
│     │     ├─ Read 'work-order.json' + 관련 source                       │
│     │     ├─ ai-commit.sh baseline → begin (branch)                     │
│     │     ├─ Edit/Write 파일 (guard.json 안 막힌 것만)                   │
│     │     ├─ ai-commit.sh gate (validator score >= baseline)            │
│     │     ├─ Task tool: subagent_type=curator-reviewer                  │
│     │     │     └─ adversarial 5-axis check → approve/reject/revise     │
│     │     ├─ approve → ai-commit.sh commit                              │
│     │     ├─ reject → revert branch, LEARNINGS append                   │
│     │     └─ Return JSON output                                         │
│     └─ 결과 surface to user                                             │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ 2. TEST (rubric.yaml 80+ checks 실행)                                   │
│                                                                         │
│   make validate-pN  →  validator/runner.py                              │
│     ├─ load rubric.yaml                                                 │
│     ├─ for each check: dispatch.py (bash/sql/http/python/e2e)           │
│     ├─ aggregate → report.json + report.md                              │
│     └─ exit code: 0=pass / 1=blocker fail / 2=score<90%                │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ 3. VERIFY (cross-tenant + RAG quality + E2E)                            │
│                                                                         │
│   • verify_rls.py    : 마커 삽입 → cross-tenant probe → 0 leak          │
│   • golden_queries   : 40 query → recall@k + MRR                        │
│   • e2e_runner.py    : Playwright 8 flows × repeat 5 → flake rate       │
│   • lib_security     : 5 prompt injection payloads → all blocked        │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ 4. LOOP (50-iter Ralph 또는 supervisor 200-iter)                        │
│                                                                         │
│   bash ralph.sh                                                         │
│     ├─ for i in 1..50:                                                  │
│     │     ├─ claude -p --dangerously-skip-permissions < ITER_PROMPT    │
│     │     │   (subprocess: 신선한 context, Task dispatch 가능)          │
│     │     ├─ check converged | stalled | cost_exhausted                 │
│     │     ├─ append JOURNAL.md, update STATE.json                       │
│     │     └─ exit if converged                                          │
│     │                                                                   │
│     └─ supervisor.sh                                                    │
│           └─ ralph crash → restart (max 15회, cooldown 60s)             │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 누가 무엇을 부르나 (call graph)

```
USER 또는 sleep
   │
   └─ /curator-validate p1 loop  ─────►  curator-loop-orchestrator (subagent)
                                              │
                                              ├─ make validate-loop PHASE=p1
                                              │     │
                                              │     ▼
                                              │   ralph.sh (200 iter)
                                              │     │
                                              │     ├─ each iter: claude -p subprocess
                                              │     │     │
                                              │     │     ├─ Task: curator-validator
                                              │     │     │   ├─ 진단
                                              │     │     │   └─ Task: curator-coder (TIER-2)
                                              │     │     │       ├─ ai-commit.sh begin
                                              │     │     │       ├─ Edit
                                              │     │     │       ├─ ai-commit.sh gate
                                              │     │     │       ├─ Task: curator-reviewer
                                              │     │     │       ├─ ai-commit.sh commit
                                              │     │     │       └─ Return JSON
                                              │     │     └─ Append JOURNAL
                                              │     │
                                              │     └─ converged → exit 0
                                              │
                                              └─ Read convergence.md → return summary
```

---

## 각 단계 entry point

| 작업 | 명령 |
|---|---|
| **개발** (TIER-2 bug 1개 처리) | `Task tool subagent_type=curator-coder` |
| **테스트** (single phase) | `make validate-p0` (or p1/p2/p3) |
| **검증** (RAG quality) | `make validate-loop PHASE=p1 --only-layers L4` |
| **검증** (RLS leak) | `make verify-rls` |
| **검증** (E2E) | `make validate-loop PHASE=p2 --only-layers L11` |
| **루프** (50 iter, Ralph) | `bash harness/ralph/ralph.sh` |
| **루프** (auto-restart, supervisor) | `bash harness/ralph/supervisor.sh` |
| **모니터** (live status) | `bash harness/monitor/watch.sh` |
| **자가복구** (medic) | `Task tool subagent_type=curator-medic` |

---

## 핵심 contract files

| 파일 | 역할 |
|---|---|
| `validator/rubric.yaml` | 80+ check 정의 (4 phase × 11 layer) |
| `validator/recipes.yaml` | 4-tier 자동 처리 정책 |
| `validator/e2e_spec.yaml` | 8 Playwright flow + assertion |
| `validator/golden_queries.yaml` | 40 RAG eval Q&A |
| `validator/manifests.yaml` | SSL skill manifests (Liang 2026) |
| `.claude/guard.json` | 영원히 사람만 (3 critical files) |
| `.claude/agents/curator-*.md` | 10 subagents |
| `.claude/skills/curator/*` | 13 slash command skills |
| `harness/ralph/RALPH_PROMPT.md` | claude -p iteration 입력 |
| `harness/ralph/TODO.md` | 진행 체크리스트 |
| `harness/ralph/JOURNAL.md` | append-only 실행 로그 |
| `harness/ralph/LEARNINGS.md` | 누적 교훈 (반복 회피) |
| `harness/ralph/STATE.json` | iter / phase / cost 상태 |
| `harness/scripts/ai-commit.sh` | branch-per-change protocol |
| `harness/scripts/setup-env.sh` | 8-stage 환경 자가복구 |

---

## 다음 세션이 진짜로 할 것 (P0)

1. **`Task` tool로 curator-coder 호출 — 한 번이라도** (이전 세션은 0회)
2. **`Task` tool로 curator-reviewer 호출** — coder의 산출물 cross-check
3. **`bash supervisor.sh` 백그라운드 실행 — 실제 5 iter** (이전 세션은 0회)

이 3개가 안 되면 자동화가 안 된 것. 시간 다 가도 이 3개부터.

---

*Last updated: 2026-05-08*
*Reference: SESSION_HANDOFF_2026-05-08.md, NEXT_SESSION_PROMPT.md*
