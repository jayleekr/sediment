# Sediment — Validation Plan (v0.2)

> 작성일: 2026-05-05
> 본 문서: 자동 validator로 Phase 0~3 완성도를 정량 측정하고, 통과한 Phase만 다음으로 넘어가는 게이팅 시스템 + **screenshot E2E 레이어 + 50-iteration self-improving loop**.
> 핵심 원칙: **Jay는 테스트/디버깅 안 함. validator + auto-fixer 루프가 한다.** Jay는 `make validate-loop PHASE=p1` 한 번 걸어두고 결과만 본다.
> v0.2 추가: L11 (E2E screenshot), Test→Dev convergence loop (max 50 iter), E2E test spec format.

---

## 0. 운영 모델 (v0.2 — self-improving loop)

```
              ┌────────── make validate-loop PHASE=p1 ──────────┐
              │                                                 │
              ▼                                                 │
┌──────────────────────┐                                        │
│  iteration N (1..50) │                                        │
│                      │                                        │
│  ① validator/runner.py                                        │
│     · rubric.yaml ── 80+ declarative checks                   │
│     · golden_queries (40 RAG eval cases)                      │
│     · e2e_spec.yaml ── browser flow (Playwright + screenshot) │
│                                                               │
│  ② report.json + report.md + screenshots/iter-N/              │
│                                                               │
│  ③ converged?                                                 │
│     · all blockers pass + score ≥ 95% + E2E green             │
│     │                                                          │
│     ├─ YES → exit 0, write convergence_report.md ─────────────┘
│     │
│     └─ NO → fixer pass (Claude Code subagent or work-order)
│              · reads failed checks + their actual values
│              · writes targeted patches OR work-order.json
│              · loop back to ① (next iteration)
└──────────────────────┘
```

**Jay의 작업 사이클**:
1. 코드 변경
2. `make validate-loop PHASE=p1` 실행 → 백그라운드에서 자동 50회 iterate
3. iteration history.csv + 마지막 report.md 만 본다
4. converged → 다음 phase
5. 50회에도 미수렴 → 마지막 work-order.json 검토 후 수동 개입

**단발 실행도 가능**: `make validate-pN` (1회). 빠른 점검용.

---

## 1. 채점 공식

각 check는 3개 severity 중 하나:

| Severity | Weight | 의미 | 1개 실패 시 |
|---|---|---|---|
| **blocker** | 10 | 없으면 제품 동작 불가 | **즉시 fail (exit 1)** |
| **major** | 3 | 동작은 하지만 품질 미달 | 점수 차감만 |
| **minor** | 1 | 있으면 좋음, 없어도 운영 가능 | 점수 차감만 |

### Phase 통과 조건 (모두 충족)
1. **모든 blocker 통과** (예외 없음)
2. **총점 ≥ 90%** of max (= sum of all weights)
3. **각 layer (L1~L10) 별 통과율 ≥ 80%** (한 layer 몰빵 방지)

### 점수 산출
```
phase_score = sum(check.weight if check.passed else 0)
phase_max   = sum(check.weight for all checks in phase)
phase_pct   = phase_score / phase_max * 100
```

---

## 2. Phase별 통과 기준 (요약)

### Phase 0 — Scaffolding ✅ 이미 wired

목표: 인프라 + DDL + RLS + 의사결정 문서 갖춤.
**Layers covered**: L1 (Unit infra), L3 (RLS).

| Group | Check 수 | Blocker 수 | 통과 조건 |
|---|---|---|---|
| INFRA (docker, deps) | 5 | 3 | postgres+redis 가동, pgvector 활성 |
| DDL (14 tables) | 4 | 4 | 모든 테이블 + 컬럼 존재 |
| RLS (정책 + 역할) | 8 | 8 | 13개 정책, 2 roles, FORCE 켜짐 |
| DECISIONS/META | 4 | 0 | 20개 질문 답변 + .env.example 완전성 |

**총 21개 check, blocker 15개. exit 조건: 15/15 + score ≥ 90%.**

### Phase 1 — Read-only 인덱스

목표: 골든 40 쿼리 중 80% 이상 정답 ref가 top-3에 포함, RLS leak = 0.
**Layers**: L1, L2, L3, L4 (RAG).

| Group | Check 수 | Blocker 수 | 통과 조건 |
|---|---|---|---|
| SEED (tenant + members) | 5 | 4 | hypeproof-lab + acme-test + 8 members |
| INGEST (ingester health + idempotency) | 6 | 3 | 1500+ chunks, 50+ artifacts |
| CHUNK (분할 정확) | 3 | 0 | heading split + overlap 동작 |
| SEARCH (hybrid retrieval) | 4 | 1 | top-3 hit rate ≥ 80% |
| RLS regression | 5 | 5 | leak count = 0 in 4 paths |
| GOLDEN-RAG (40 queries) | 40 | 0 | recall@3 ≥ 80%, MRR ≥ 0.5 |

**총 63 check, blocker 13개.**

### Phase 2 — Chat MVP

목표: SSE round-trip + intent 라우팅 + RLS during chat + p95 latency + **E2E browser flow**.
**Layers**: L2, L3, L5, L6 (security), L8 (perf), L9 (observability), **L11 (E2E)**.

| Group | Check 수 | Blocker 수 |
|---|---|---|
| Service health (4 ports) | 5 | 4 |
| Auth (JWT round-trip) | 4 | 2 |
| Conversation CRUD | 5 | 3 |
| SSE protocol (events: message/delta/citation/[DONE]) | 8 | 4 |
| Intent routing (4 intents) | 4 | 0 |
| MCP server (12 tools) | 4 | 0 |
| Web routes (5 pages) | 5 | 2 |
| RLS during chat | 4 | 4 |
| Security (5 prompt injection payloads) | 5 | 0 |
| Performance (p95 latency targets) | 4 | 0 |
| **E2E browser (8 flows × screenshot)** | **8** | **3** |

**총 56 check, blocker 22개.**

### Phase 3 — Ingest 자동화

목표: cron + GHA + Discord ingest 가 syntax error 없이 실행 가능.
**Layers**: L1, L9.

| Group | Check 수 | Blocker 수 |
|---|---|---|
| Shell scripts (executable + dry-run) | 4 | 1 |
| launchd plists (plutil -lint) | 2 | 0 |
| GitHub Action (actionlint) | 2 | 0 |
| Discord ingest (fixture + insert) | 3 | 0 |
| Retro/Dream (idempotent + RLS-aware) | 4 | 1 |

**총 15 check, blocker 2개.**

---

## 3. Validator 실행

### 단일 phase
```bash
make validate-p0   # Phase 0만
make validate-p1   # Phase 1만 (P0 자동 선행 X — 명시적으로)
make validate-p2
make validate-p3
```

### 전체
```bash
make validate-all  # P0 → P1 → P2 → P3 순차. 어디 하나 실패하면 멈춤.
```

### Watch 모드 (개발 중)
```bash
make validate-watch PHASE=p1   # services/, infra/, scripts/ 변경 감지 → 자동 재검증
```

### 출력
- 콘솔: 진행률 + 색상 결과 (rich)
- `output/validation/<phase>-<timestamp>.json` — 머신 판독 (CI 게이트)
- `output/validation/<phase>-<timestamp>.md` — 사람 판독
- `output/validation/latest.{json,md}` — 가장 최근 (symlink)

### 출력 예시 (요약)
```
╭──────────────── Validation Report — Phase P0 ────────────────╮
│  ✅ INFRA           5/5    (15/15 weight)                    │
│  ✅ DDL             4/4    (40/40)                           │
│  ✅ RLS             8/8    (80/80)                           │
│  ⚠️  DECISIONS_META  3/4   (8/9)  — P0-DECISIONS-04 minor    │
├─────────────────────────────────────────────────────────────┤
│  Total: 20/21 checks · 143/144 weight · 99% · PASS           │
│  Layer L1: 100% · L3: 100%                                   │
│  Blockers: 15/15 ✅                                          │
│  Elapsed: 3.2s · LLM cost: $0.00                             │
╰─────────────────────────────────────────────────────────────╯
```

### 실패 예시
```
╭──────────────── Validation Report — Phase P1 ────────────────╮
│  ✅ SEED            5/5    (28/28)                           │
│  ❌ INGEST          4/6    (8/14)  ← 2 blockers failed       │
│      P1-INGEST-03  POST /v1/ingest/document → 502 Bad Gateway│
│      P1-INGEST-04  re-ingest creates 2× chunks (idempotency) │
│  ⚠️  CHUNK           2/3    (4/5)                            │
│  ❌ GOLDEN-RAG      24/40   (24/40)  ← recall@3 = 60%        │
├─────────────────────────────────────────────────────────────┤
│  Total: 35/63 checks · 64/144 weight · 44% · FAIL            │
│  Blockers: 11/13 ❌                                          │
│  Action: 위 2개 blocker 수정 후 make validate-p1 재실행       │
╰─────────────────────────────────────────────────────────────╯
exit code: 1
```

---

## 4. Phase 진행 권장 순서 (validator 게이트 결합)

```
[D-1: install + seed + 1차 ingest]
  ↓ make validate-p0
  ↓ blocker fail → 인프라 수정 → 재실행
  ↓ exit 0
  
[Phase 1: Search/RAG 튜닝]
  ↓ chunker 파라미터 조정, golden 40 쿼리 라벨링
  ↓ make validate-p1  
  ↓ recall@3 < 80% → 임베딩/청킹 조정 → 재실행
  ↓ exit 0
  
[Phase 2: Chat 동작 + 적대적 입력 견디기]
  ↓ SSE 디버깅, 인텐트 분기 조정
  ↓ make validate-p2
  ↓ TTFT > 2s → 스트림 최적화 → 재실행
  ↓ exit 0
  
[Phase 3: 자동화 hook (선택)]
  ↓ 솔로 사용엔 cron 불필요 → P3 skip 가능
  ↓ 외부 베타 진입 시 make validate-p3 통과 필요
```

**솔로 MVP 최소 패스**: P0 → P1 → P2 통과면 충분.
**외부 베타 진입 패스**: P0 → P1 → P2 → P3 + (Phase 5.5 dogfood gate, TEST_REQUIREMENTS.md §5).

---

## 5. Phase가 통과 못 하는 흔한 패턴 + 해결

| 증상 | 원인 후보 | 자동 진단 |
|---|---|---|
| P0-RLS-01~08 fail | init.sql 미적용 / `FORCE ROW LEVEL SECURITY` 빠짐 | `make reset && make seed` |
| P1-INGEST-01 fail (ingester unreachable) | 서비스 미가동 / 포트 충돌 | runner가 `nc -z localhost 11000` 사전체크 |
| P1-GOLDEN-RAG recall < 80% | 청킹 너무 큼 / 임베딩 모델 불일치 / corpus 부족 | runner가 `chunks_count`, `avg_chunk_tokens` 동시 보고 |
| P2-SSE-* fail (no events) | langgraph 미가동 / Anthropic key 없음 (offline mode) | runner가 ANTHROPIC_API_KEY 존재 + 미스 시 offline모드 명시 |
| P2-RLS-02 fail (cross-tenant leak) | TenantContextMiddleware 미적용 | **즉시 release block** |
| P3-CRON-02 fail (dream.py error) | tenant 0개 또는 SQL syntax | runner가 stderr를 그대로 출력 |

---

## 6. 점수 추적 (시간순)

`output/validation/history.csv` — 매 실행마다 1 row 추가.

```csv
timestamp,phase,score,max,pct,blockers_passed,blockers_total,passed
2026-05-05T15:00:00Z,P0,143,144,99,15,15,true
2026-05-05T16:30:00Z,P1,64,144,44,11,13,false
2026-05-05T18:10:00Z,P1,128,144,89,13,13,false   # blockers 통과했지만 90% 미달
2026-05-05T19:00:00Z,P1,135,144,94,13,13,true
```

이 데이터로 **Phase 1을 90%까지 끌어올리는 데 며칠 걸렸는지**, **어느 항목이 자주 회귀하는지** 추적 가능.

---

## 7. CI 게이트 (`.github/workflows/curator-validate.yml` — Phase 6+)

```yaml
on: pull_request
jobs:
  validate:
    runs-on: ubuntu-latest
    services: [postgres, redis]
    steps:
      - run: make up && make seed
      - run: make validate-p0   # blocker fail = PR block
      - run: make validate-p1
      # P2/P3는 라벨 기반 (긴 시간)
```

main merge 차단 규칙:
- `make validate-p0` 실패 = block
- `make validate-p1` blocker 실패 = block
- score regression > 5pp = warn (label `validation-regression`)

---

## 8. validator 자체 검증 (메타)

validator도 코드. validator가 깨지면 false-positive로 통과 보고 가능.
→ `tests/validator/test_dispatcher.py` — bash/sql/http/python check type별 단위 테스트
→ `tests/validator/test_rubric_schema.py` — rubric.yaml 스키마 검증 (severity 누락 등)

`make test` 안에 포함. validator 자체는 항상 unit test 통과해야 함.

---

---

## 9. L11 — E2E Browser Layer (NEW v0.2)

기존 L1-L10 위에 **L11 = 실제 브라우저로 사용자 시나리오 + 스크린샷**.

### 9.1 도구 선택
- **Playwright (Python)** — 가장 안정적, async 네이티브, 스크린샷 1급, 헤드리스 + 헤디드 둘 다.
- 선택 이유: claude-in-chrome MCP는 dev-time인터랙티브용. CI/automated 검증은 Playwright이 표준.

### 9.2 E2E 시나리오 (8개)

| ID | 시나리오 | 단계 | screenshot 횟수 |
|---|---|---|---|
| E2E-01 | Sign-in flow | open `/curator` → input email → submit → see chat | 3 |
| E2E-02 | Empty state visible | post sign-in → assert empty conv list + suggested queries | 1 |
| E2E-03 | New conversation + first query | click suggestion → URL 변경 `/curator/c/[id]` → SSE 시작 | 4 |
| E2E-04 | Streaming UI | 첫 delta 도착 → 누적 → answer_end | 3 (50%, 100%, end) |
| E2E-05 | Citation cards visible | sidebar에 citation list 렌더 | 1 |
| E2E-06 | Library browse + filter | `/curator/library` → click `column` filter → table updates | 2 |
| E2E-07 | Members page | `/curator/members` → 8 cards visible | 1 |
| E2E-08 | Cross-tenant negative test | acme tenant 토큰으로 hypeproof-lab의 conv URL 접근 → 403 또는 empty | 2 |

각 screenshot은 `output/validation/screenshots/iter-N/E2E-XX-step-Y.png`로 저장.

### 9.3 시각 회귀 (선택, Phase 5+)
- baseline screenshot vs current screenshot pixel-diff (allowed delta 0.5%)
- 의도적 UI 변경 시 `make e2e-update-baseline`으로 갱신

### 9.4 Console / Network 검증
- 모든 페이지에 `console.error` 0건 (제외 목록: 알려진 dev warning)
- 401/500 응답 0건 (sign-in 전 의도된 401 제외)
- 페이지 load p95 ≤ 2.0s

### 9.5 환경 구성
```bash
# Phase 0의 install 단계에 추가:
make install            # Python deps + playwright
make e2e-install        # playwright install chromium (1회)

# 실행
make validate-e2e PHASE=p2     # E2E만 단독 실행
make validate-p2               # E2E 포함 전체 P2 검증
```

### 9.6 E2E spec 형식 (e2e_spec.yaml — declarative)

```yaml
flows:
  - id: E2E-01
    name: "Sign-in flow"
    severity: blocker
    base_url: "http://localhost:3000"
    steps:
      - action: navigate
        url: "/curator"
        wait_for: "selector=input[placeholder='member email']"
        screenshot: "01-empty-form"
        assert:
          - type: text_contains
            selector: "h2"
            value: "Sign in"
      - action: fill
        selector: "input[placeholder='member email']"
        value: "jayleekr0125@gmail.com"
        screenshot: "02-filled"
      - action: click
        selector: "button:has-text('Mint dev token')"
        wait_for: "selector=text=Conversations"
        screenshot: "03-after-signin"
        assert:
          - type: url_contains
            value: "/curator"
          - type: localStorage_has
            key: "curator.token"
          - type: console_errors_count
            max: 0
```

### 9.7 E2E 안정성 — 50회 자동 반복

E2E는 flaky하기 쉬우므로 **단일 iteration 내에서도 핵심 flow는 5회 반복**:
```yaml
- id: E2E-04
  name: "Streaming UI"
  repeat: 5     # 같은 flow를 5회 → flake rate 측정
  pass_threshold: 4   # 5회 중 4회 이상 통과해야 OK
```

전체 loop은 50 iteration 이므로 E2E-04는 **누적 250회 실행** → flake rate < 5% 보장.

---

## 10. Self-Improving Loop (50 iterations)

### 10.1 루프 알고리즘
```python
def loop(phase: str, max_iter: int = 50, target_score: int = 95):
    history = []
    for i in range(1, max_iter + 1):
        report = run_validator(phase)
        history.append(report)

        if report.all_blockers_passed and report.score_pct >= target_score:
            write_convergence_report(history)
            return 0  # converged

        # generate work-order from failures
        wo = build_work_order(report)
        write_work_order(f"iter-{i}/work-order.json", wo)

        # auto-fix where possible (declarative recipes)
        applied = auto_fix(wo)
        if applied == 0 and i > 5:
            # 5회 연속 fixer가 손 못 대면 정체 → 사람 호출
            write_stalled_report(history)
            return 2

    write_max_iter_report(history)
    return 1
```

### 10.2 Auto-fixer 처리 가능 vs 불가

| 실패 유형 | 자동 가능? | 처리 방법 |
|---|---|---|
| `make up` 안 됨 (docker 미실행) | ✅ | `docker compose up -d` 실행 |
| `seed` 안 됨 | ✅ | `make seed` 호출 |
| `ingester` 미가동 (port closed) | ✅ | background로 `make ingester` 띄움 (timeout 30s) |
| init.sql 미적용 | ✅ | `psql -f infra/init.sql` |
| RLS leak (코드 버그) | ❌ | **work-order.json 작성 후 사람 호출** |
| Golden RAG recall 낮음 | ❌ | 청킹/임베딩 튜닝은 사람 판단 |
| E2E selector 깨짐 (UI 변경) | ❌ | 사람이 selector 갱신 |
| Prompt injection 통과됨 | ❌ | 시스템 프롬프트/가드 사람 강화 |

자동 가능 = recipe이 `validator/recipes/*.yaml`에 정의된 것.
나머지는 work-order.json만 produce → Jay 또는 Claude Code 수동 dispatch.

### 10.3 history.csv (50 iter 추적)

```csv
iter,phase,score,max,pct,blockers_passed,e2e_flake_rate,fixer_actions,elapsed_s
1,p1,42,144,29,8,N/A,3,18
2,p1,68,144,47,11,N/A,2,22
3,p1,89,144,62,12,N/A,1,25
...
17,p1,142,144,99,13,3.2%,0,28   ← converged!
```

**수렴 곡선이 평탄해지면 (5 iter 동안 score 변화 < 2pp) 정체 → 사람 호출.**

### 10.4 iteration 비용 가드

각 iteration LLM cost 한도:
- RAG eval (golden 40 queries × judge cost) ≈ $1.0/iter
- E2E (no LLM) ≈ $0.0/iter
- 50 iter × $1 = **$50/run** 한도

`COST_BUDGET_VALIDATION_LOOP_USD=50` 환경변수로 stop. 초과 시 즉시 종료 + 부분 보고서.

---

## 11. Test Spec for E2E (별도 spec — Jay 요청 v0.2)

E2E flow 자체도 spec 기반으로 검증 가능해야 함. 이중 검증.

### 11.1 e2e_meta_spec.yaml — E2E spec 자체의 contract
```yaml
required_fields_per_flow:
  - id
  - name
  - severity
  - steps
required_fields_per_step:
  - action
allowed_actions: [navigate, fill, click, wait, screenshot, assert]
allowed_assertions: [text_contains, url_contains, selector_visible, 
                     localStorage_has, console_errors_count, network_404_count]
```

### 11.2 E2E spec lint
```bash
make validate-e2e-spec
# 1. e2e_spec.yaml schema 적합?
# 2. 모든 flow에 최소 1 screenshot?
# 3. 모든 selector이 selector syntax 인지 확인?
# 4. base_url이 reachable?
# pass → spec 자체 OK, 실제 flow 실행 가능
```

### 11.3 다회 반복 + flake rate
- 핵심 flow (E2E-01, 03, 04, 08) repeat=5
- 보조 flow (E2E-02, 05, 06, 07) repeat=2
- iteration 내 누적 25-30 flow run × 50 iter = **1250-1500 flow run**

flake rate가 5% 초과 → **L11 E2E 자체 실패** → loop 더 못 돌림 → 사람 호출.

---

*Last updated: 2026-05-05 (v0.2 — E2E + 50 iter loop 추가)*
*다음: rubric.yaml + e2e_spec.yaml 작성 → validator/runner.py 구현 → loop 실행으로 P0 수렴.*
