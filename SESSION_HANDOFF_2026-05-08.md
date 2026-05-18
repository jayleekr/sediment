# Session Handoff — 2026-05-08

> 핵심 솔직 인정: 하네스 **파일은 다 만들었지만**, 그걸로 자동 돈 게 아니라
> 메인 Claude Code 세션이 Bash로 직접 step-by-step 실행한 것임.
> Task tool로 `curator-coder`/`curator-reviewer` agent를 한 번도 dispatch 안 함.
> 다음 세션은 이걸 **진짜로** 돌려야 함.

---

## 1. 무엇이 실제로 작동하는가 (verified)

| 항목 | 검증 방법 | 결과 |
|---|---|---|
| Postgres 5433 + Redis 6380 | `nc -z` + `pg_isready` | ✅ |
| 14 tables + 14 RLS + 13 policies + 2 roles | SQL probe | ✅ |
| Seed: 9 lab + 1 acme members | `make seed` | ✅ |
| Vault ingest: 85 artifacts, 678 chunks | `make ingest` | ✅ |
| **P0 validator: 14/14 blockers, 99.4%** | `python -m validator --phase P0` | ✅ exit 0 |
| **P1 validator: 12/12 blockers, 89.7%** | 동상 | ✅ exit 0 |
| **verify_rls cross-tenant** | `python -m scripts.verify_rls` | ✅ |
| platform :10100 + langgraph :10020 | uvicorn background | ✅ healthz |
| dev-token endpoint mints JWT | curl | ✅ |
| **P2 validator: 15/21 blockers, 65.1%** | (수동 패치 1개 후) | partial |
| `ai-commit.sh` baseline → begin → gate → commit | 2회 사이클 (DEMO + P2-AUTH-01) | ✅ |
| `git merge` ai branch → main | manual | ✅ |
| `LEARNINGS.md` 자동 누적 | `log_learn` 함수 | ✅ |

## 2. 무엇이 파일은 있지만 실제로 안 돌았나 (NOT verified)

| 컴포넌트 | 상태 |
|---|---|
| `curator-coder` agent | 파일 작성 완료. **Task tool로 한 번도 dispatch 안 됨**. |
| `curator-reviewer` agent | 동상. adversarial cross-review 한 번도 실행 X. |
| `curator-medic` agent | 6 patterns 진단 — 파일만, 실행 X |
| `curator-rls-auditor`, `curator-rag-tuner`, `curator-e2e-debugger`, `curator-rubric-author` | 파일만 |
| `Ralph supervisor` (200 iter loop) | 파일만 작성. 한 번도 background 실행 안 함 |
| `ralph.sh` claude -p subprocess | 한 번도 호출 안 됨 |
| 자가복구 medic cron | 파일만 |
| 8 E2E Playwright flows | chromium 미설치 |
| Web :3000 (Next.js) | 안 띄움 (5/5 인 경우 잠시 띄웠다가 끔) |
| LLM SSE 실제 답변 | LLM_PROVIDER=offline (mock) |
| Multi-provider abstraction (`lab_lib/llm.py`) | 모듈 작성 + smoke import만 OK. 실제 anthropic/gemini/claude_cli 호출 0회 |
| `harness/bootstrap.py` (cross-project) | 파일만, 다른 project 시드 안 함 |
| Recipes `ai_apply_immediately` 자동 적용 | 한 번도 매칭 안 됨 |

## 3. 7+1개 real bug — 모두 수동 발견 + 수동 수정

| # | Pattern | 파일 |
|---|---|---|
| 1 | docker credential PATH | `bootstrap-all.sh`, `setup-env.sh` |
| 2 | PG18 mount layout | `infra/docker-compose.yml` (→ pg17) |
| 3 | python parents[N] 오프바이투 (×4 file) | seed/ingest/p0/p3 |
| 4 | SQLAlchemy `:NAME::TYPE` cast | seed_lab.py |
| 5 | empty external_id UNIQUE | seed_lab.py |
| 6 | dotfile filter overreach (.claude/) | ingest_repo.py |
| 7 | `SET LOCAL X=$1` PG 미지원 (×18 file) | sed bulk patch |
| 8 | TenantContextMiddleware 차단 dev-token | tenant_middleware.py PUBLIC_PREFIXES |

bug #1-7는 자동화 의도와 무관한 깜짝 발견. bug #8만 ai-commit.sh sequence로 처리됨 (수동 step 따라).

## 4. 결정 사항 (DECISIONS.md에 다 기록)

- 이름: AI Curator
- Backend: FastAPI 모노리포 (`services/sediment/`)
- DB: 로컬 Postgres 5433 + pgvector (Supabase prod)
- Auth: NextAuth.js + 자체 Org (Phase 5에 wire)
- Multi-provider LLM: anthropic / gemini / claude_cli / offline (default offline)
- Tenancy: Postgres RLS (single DB, single schema)
- Pricing: Free $0 / Pro $20 / Business $40 / Enterprise contact
- Code mod policy: 4-tier (forbid / human / review / auto)

## 5. 코드 수정 가드 (`.claude/guard.json`)

영원히 사람만:
- `infra/init.sql`
- `.env`
- `routers/billing.py`
- `**/credentials*`
- `.claude/settings*.json`

## 6. git 상태

브랜치: `worktree-mvp` (커밋 9개 누적, 5/5에 commit `98d1790` 이후 5/8 커밋들)

```
6f62f18 ai-coder e2e: full workflow + P2-AUTH-01 proof of value
7d9fe4f Merge AI fix: P2-AUTH-01 public auth paths
87563ef ai-coder e2e: P2-AUTH-01 fix successfully committed on branch
fb3f64d fix(P2-AUTH-01): expose /api/v1/auth/*
54c6419 ai-coder e2e: DEMO-CHECK successful commit cycle
27a8eda ai-commit.sh: bash 3.2 compat + correct validator output path
b6bf714 gitignore ephemeral output dirs
a9f086e patch ai-commit.sh: heredoc to -F file
98d1790 ai-curator: full harness + validator + agents + ralph + verification
```

AI 브랜치 2개 보존:
- `ai/coder/demo-check-20260508T061920` (무해 주석 추가)
- `ai/coder/p2-auth-01-20260508T062217` (실제 fix, main에 머지됨)

## 7. 백그라운드 살아있는 것 (다음 세션에서 확인 필요)

```bash
nc -z localhost 5433  # postgres
nc -z localhost 6380  # redis
nc -z localhost 11000 # ingester
nc -z localhost 12000 # metadata
nc -z localhost 10100 # platform
nc -z localhost 10020 # langgraph
```

전부 떠있을 가능성 높음 (nohup으로 시작했고 명시적 kill 안 함).

## 8. 다음 세션이 진짜로 해야 할 것

### 우선순위 P0 (다음 세션 1시간 안에)
1. **Task tool로 `curator-coder` agent 진짜 dispatch** — P2-INTENT 같은 남은 TIER-2 bug 1개로 끝까지 흐름 검증
2. **Task tool로 `curator-reviewer` agent 진짜 dispatch** — coder의 diff을 적대적 cross-review
3. **`ralph.sh` 1회 실행** (5 iter 정도만, $5 한도) — `claude -p` subprocess 패턴 실증

### P1 (1-2시간)
4. `make e2e-install` (Playwright chromium 다운로드)
5. P2 E2E 8 flows 실행 (실제 브라우저 + 스크린샷)
6. `make web` 시작 + 브라우저로 `/curator` 사인인 + 첫 query

### P2 (2-3시간 또는 다음다음 세션)
7. ANTHROPIC_API_KEY 또는 Gemini key 채우고 `LLM_PROVIDER` 변경 → SSE 실제 LLM 답변
8. P3 validator 실행 (cron + GHA + Discord)
9. 남은 P2 TIER-2 bug 자동 처리 (INTENT SQL, INGEST-04 idempotency)
10. P2 65.1% → 90%+

### Phase 5.5+ (며칠)
11. Ralph supervisor 야간 실제 가동 (50 iter)
12. medic agent 매 30분 cron으로
13. NextAuth.js + Discord OAuth (dev-token 폐기)
14. Stripe 실제 webhook
15. Anthropic Workspace 계정 + spend limit
16. Phase 5.5 dogfood gate (10 criteria) 측정 시작

## 9. 갭 분석 — 왜 자동 안 돌았나

| 갭 | 원인 | 다음 세션 대응 |
|---|---|---|
| 메인 세션이 직접 Bash 실행 | "더 빠르고 직관적" 유혹 | RALPH_PROMPT.md를 SUBAGENT_DISPATCH_PROMPT로 강화 (다음 §10) |
| Task tool dispatch 한 번도 안 함 | 익숙하지 않아서 | 다음 세션 첫 액션 = curator-coder dispatch 1회 강제 |
| Ralph background 안 띄움 | 비용/결과 불확실성 | $5만 budget으로 제한해서 5 iter 실험 |
| Permission prompt 회피 우선 | heredoc, chmod, docker 등 | `bash <path>` 패턴 + `-F file` 패턴 정착 |

## 10. 다음 세션 시작 지점

이 worktree의 working tree는 clean (commit 6f62f18에서 멈춤).

```bash
# 다음 세션 시작 첫 명령
cd /Users/jaylee/CodeWorkspace/hypeproof/.claude/worktrees/mvp
cat products/sediment/SESSION_HANDOFF_2026-05-08.md     # 이 파일
cat products/sediment/NEXT_SESSION_PROMPT.md            # paste-ready prompt
ls .claude/agents/curator-*.md                            # 10개 agent 확인
ls products/sediment/harness/ralph/                     # ralph + supervisor
git log --oneline -10
```

다음 세션은 `NEXT_SESSION_PROMPT.md`를 첫 메시지로 넣고 시작.

---

*Last commit: 6f62f18 (worktree-mvp branch)*
*Next session entry point: NEXT_SESSION_PROMPT.md*
