# DECISIONS — Sediment §11 답변

> 작성일: 2026-05-05
> 결정자: Jay (위임 — "내 의견 없이 진행" 지시)
> 본 문서: SPEC.md §11 1~20번 결정 + 근거. 변경 시 SPEC v0.3로 동기화.

---

## §11.1 제품/기술 결정 (1~10)

| # | 질문 | 결정 | 근거 |
|---|---|---|---|
| 1 | 이름 | **Sediment** | AIT mirror, 영문이라 도메인/SEO 우호적, 의미 명확 ("editor"는 Editorial Director persona와 충돌) |
| 2 | 백엔드 스택 | **Python FastAPI 모노리포** (`services/sediment/`) | AIT 패턴 그대로. multi-tenant + LangGraph 친화. Next.js API Routes는 streaming/long-task에 약함 |
| 3 | DB | **로컬 Postgres+pgvector via docker-compose** (개발) → **Supabase** (prod 후보) | RLS 네이티브 지원. Vercel Postgres는 RLS 제한적. Neon은 branch는 좋지만 RLS 미흡 |
| 4 | Auth | **NextAuth.js + 자체 Organization 모델** (MVP) → Phase 11 enterprise 시 WorkOS 마이그레이션 | Clerk는 $25/mo 시작 — 로컬 dogfood엔 오버. NextAuth + 자체 org는 1주 작업, 비용 0 |
| 5 | Ingest 우선순위 | **`research/daily/` → `web/src/content/columns/` → 회의록 → Discord** | daily research 가장 양 많고 frontmatter 정제됨. 칼럼은 KO/EN 페어 → 검증 쉬움 |
| 6 | 개인-팀 메모리 | **명시적 `/curator publish` 명령으로만** Jay 개인 메모리 → 팀 vault 승격 | 자동 동기화 = 데이터 누출 리스크. 인지 partner와 팀 product 분리 |
| 7 | MCP 위치 | **`services/sediment/lab_platform/mcp_servers/workspace_mcp.py`** | 외부 SaaS 고객은 Claude Code 안 씀. 별도 포트(8888)로 expose, FastMCP 사용 |
| 8 | 첫 사용자 그룹 | **Jay 단독 1주 → JY/Ryan 추가 → 8명** | 도그푸딩 단계 1주는 빠른 iteration용 (질문 패턴 발견, UI 버그 수정). 8명 동시는 노이즈 ↑ |
| 9 | 비용 한도 (내부) | **$200/mo** (`COST_BUDGET_MONTHLY_USD=200`) | 8명 × 25 query/day × 30일 × ~$0.03 = $180. 버퍼 $20 |
| 10 | 5/5 파일럿과의 우선순위 | **병행** (별도 worktree) | Curator 빌드는 Jay 1인 작업, 5/5 파일럿은 BH/JY/Ryan 작업. 인적 자원 충돌 없음 |

---

## §11.2 상업화 결정 (11~20)

| # | 질문 | 결정 | 근거 |
|---|---|---|---|
| 11 | Tenancy 격리 모델 | **Postgres RLS** (single DB, single schema, `tenant_id` column) | ~100 tenant까지 검증된 패턴 (Supabase 사례 다수). 마이그레이션 시 schema-per-tenant로 전환 가능 |
| 12 | Pricing 모델 | **Hybrid: seat 기반 + usage overage** | seat은 예측 가능 매출, usage overage는 power user 대응. Linear/Notion 모두 사용 |
| 13 | 무료 tier 존재? | **Yes** (3 seat / 1k query/mo / 1GB) | HypeProof Lab dogfooding이 free tier에 자연 fit. Academy 졸업생 viral 채널 |
| 14 | 출시 가격 | **Free $0 / Pro $20 / Business $40 / Enterprise contact** | Notion AI ($20)와 동일 가격대. premium 포지션 아닌 mainstream |
| 15 | Custom domain | **MVP: 서브도메인 자동** (`<slug>.curator.hypeproof-ai.xyz`) → **Phase 11**: CNAME 옵션 (Business+ 한정) | 외부 베타까진 충분. enterprise 등장 시 |
| 16 | Brand 노출 | **Free: 강제 Powered by HypeProof / Pro+: 토글 / Enterprise: white-label** | 표준 SaaS 패턴 |
| 17 | 데이터 거주 | **MVP: US 단일** (Vercel + Supabase US) | KR 고객 등장 시 (Phase 12) Naver Cloud / AWS Seoul region 추가 |
| 18 | Compliance | **Phase 6: 개인정보처리방침 + 이용약관** → **Phase 7: DPA 템플릿** → **Phase 8+: PIPA(KR)** → **Phase 10+: SOC 2 Type I** | 필요 시점에 단계적 진입. 변호사 검토 1회 $500 예산 |
| 19 | 고객 지원 채널 | **MVP: Discord 전용 server** → **Phase 9+ paid 10 tenant 이상시 Intercom 검토** | Discord 무료, community 효과. Intercom $100+/mo는 매출 충분할 때 |
| 20 | Sales motion | **MVP: Self-serve PLG only** → **Phase 9 hybrid (대형 고객 outbound)** → **Phase 10 enterprise sales** | PLG가 viral cheap. enterprise는 상위 tier 등장 시 |

---

## §11.3 1차 외부 고객 — 도그푸딩 후 결정

도그푸딩 4주 종료 시점(Phase 5.5, ~6/26)에 NPS/사용 패턴 데이터 기반 결정. 후보 우선순위 사전 정렬:

1. **동아일보 / 미디어사** — donga-roi 연장선, sales motion 명확
2. **Academy 졸업생** — 7-lens 노출, viral 채널, free tier 직결
3. **컨설팅사 (작은)** — Sebastian 네트워크, knowledge mgmt 수요
4. **CERN / 연구 그룹** — BH 네트워크, paper-lab 자산 재활용

---

## 추가 결정 (SPEC v0.2 작성 중 발생)

| 항목 | 결정 | 근거 |
|---|---|---|
| 임베딩 모델 | **OpenAI `text-embedding-3-small` (1536d)** | 비용 ($0.00002/1k token), 품질 검증, pgvector 호환 |
| LLM | **Anthropic Claude Sonnet 4.6** (메인) + **Opus 4.7** (memory consolidator) | sonnet은 query 처리, opus는 dream cron 같은 무거운 작업 |
| Markdown 청킹 | **헤딩 기준 hierarchical chunking, max 1500 tokens, overlap 200** | 작은 칼럼은 헤딩 단위로, 긴 소설/리서치는 hierarchical |
| LangGraph 진입점 | **`workspace_curator_graph` (단수)** | per-tenant 분기는 state.tenant_id로 처리, graph는 1개 |
| 검색 알고리즘 | **Hybrid: BM25 (full-text) + pgvector cosine, RRF rerank** | 벡터만으론 keyword 정확도 ↓. PostgreSQL ts_vector + RRF |
| Discord MCP | **Mother bot 재활용** (별도 구현 X) | Mother가 이미 Discord plugin으로 read/write. Curator는 RPC 호출만 |
| 환경 변수 관리 | **`.env` (gitignored) + `.env.example` (committed)** | 표준 패턴 |
| 마이그레이션 | **Alembic** | SQLAlchemy 호환, 가장 검증됨 |
| Test 프레임워크 | **pytest + pytest-asyncio + httpx (async client)** | FastAPI 표준 |
| 로깅 | **structlog + JSON output** | tenant_id, request_id 컨텍스트 자동 포함 |

---

## Multi-provider LLM strategy (2026-05-08, post-live-test)

비용 최적화를 위해 single-provider (Sonnet) 가정 폐기. **3-tier provider 매핑**:

| 환경 | Provider | 모델 | 이유 |
|---|---|---|---|
| Dev (Jay solo) | **claude_cli** subprocess | (whatever MAX uses) | $0 — Claude Code MAX 활용. 외부 결제 0 |
| Prod Free tier | **gemini** | gemini-2.5-flash | $0.075/$0.30 per Mtok. 6000 query/mo ≈ $3.6 |
| Prod Pro tier | **gemini** | gemini-2.5-pro | $1.25/$5 per Mtok. 동일 query 양 ≈ $72/mo |
| Prod Business tier | **anthropic** | claude-sonnet-4-6 | $3/$15. premium 차별화 ≈ $180/mo |
| Enterprise | **BYOK** | 고객 선택 | 자기 키 → seat fee만 받음 |
| CI / no key | **offline** | mock | 결정적, 무비용. 통합 테스트용 |

**아키텍처 변경**: `lab_lib/llm.py` 추상화. `curator_langgraph/main.py`가 직접 Anthropic SDK 호출 → `stream_chat(system, user)` 호출. provider는 env (`LLM_PROVIDER`) 또는 per-tenant flag로 결정.

**Gemini Flash 품질 검증 필요**: golden 40 query × Sonnet vs Flash A/B → faithfulness 차이 측정. 차이 < 0.05이면 Free tier는 Flash 확정.

**MAX 계정 안 함부로 굴리기**: claude_cli mode는 dev 전용. SaaS production traffic 절대 MAX로 받지 말 것 (TOS 위반 + 한도 초과).

| 항목 | 결정 |
|---|---|
| 추상화 위치 | `lab_lib/llm.py` (4 providers: anthropic, gemini, claude_cli, offline) |
| 자동 fallback | 키 없는 provider는 offline mock으로 graceful degrade |
| Per-tenant override | `tenants.feature_flags.llm_provider` (Phase 7+) |
| Embedding | OpenAI `text-embedding-3-small` 유지 (저렴) — 또는 Gemini `text-embedding-004`로 일원화 가능 |

---

## Dogfood activation gate + ship-gate (2026-05-19, ratified by Jay)

Ratifies the Activation Engine (`ACTIVATION_ENGINE.md`, overnight loop v4 9/9).
Supersedes **only the adoption clause** of `PHASE_5_5_DOGFOOD_GATE.md`.

| # | 결정 | 비준 |
|---|---|---|
| A | Adoption verdict = **S3≥5/8 + S4≥3/8** (was DAU≥5/8). Criteria 5/6/7 (DAU/query/turns) → diagnostic-only; criterion 9 (NPS) → absorbed into S4; criteria 1–4/8/10 stay hard pass/fail. S3 is judgement-assisted → **Jay override mandatory on any S3-borderline user** | ✅ |
| B | Per-member owned-task map (§4 Week-0 draft) approved as the working map, **each confirmed 1:1 with the member in Week-0** | ✅ |
| C | Champions: **Ryan (content) + JY (eng)**. (Ring-2 entry needs ≥1 non-builder champion added — noted.) | ✅ |
| D | **Ship-gate (the teeth) — HARD LINE:** demo / design-partner discovery is allowed early, but **no paying or commitment-bearing external tenant until `S3≥5/8` (Ring 1) AND `S3+` from ≥1 non-builder (Ring 2)**. Aligned with `ACTIVATION_ENGINE.md` §7 ring model (Ring 3 exit = paying-intent + non-builder S3+). | ✅ |
| E | Magic-number (`≥5 cite-into-work/10d`) and S3 threshold `X` are **placeholders**; Week-1 instrumentation **replaces** them with measured values, locked at Week-1 exit. Neither may gate until locked. | ✅ |

> **Why D matters most for the big goal.** North star (2026-06-end): Sediment
> = the Lab's living memory it can't work without + demo-able to a first
> external tenant. D is not a brake on that — it prevents burning the *first*
> design partner on a tool that hasn't yet changed internal behaviour (the
> single most expensive early-SaaS mistake).

---

*Last updated: 2026-05-19*
*Status: Multi-provider abstraction landed. Dogfood activation gate + ship-gate ratified (engine build begins).*
