# Collection & Distillation — design (v0.2)

> 2026-05-20. **v0.2 supersedes v0.1** (git: `23f961b`). Major changes from
> Jay's refinement: (a) capture는 **all-channels / all-sources** — allow-list
> 폐기, 필터링은 distill로 이동, (b) **3-layer RBAC** (platform / tenant /
> member) — SaaS 처음부터 (c) **enterprise-grade continuous feed** —
> 고객사 모든 자료를 끊김 없이 먹는 Collection AI 에이전트.

## 0. 핵심 변경 (v0.1 → v0.2)

| 영역 | v0.1 | v0.2 |
|---|---|---|
| Discord capture | 4개 채널 allow-list (#meeting-notes 등) | **모든 채널 전수집**, 필터는 distill 단에서 |
| 권한 | members.role(admin/creator/viewer) 단순 | **3-layer RBAC** + resource ACL + audit |
| Connector | dogfood 우선 3개 | enterprise catalog 15+ + 멀티테넌트 KMS + cursor-기반 continuous |
| 호스팅 | Fly supervisord 1대 | 워커 풀 (per-tenant 격리 옵션) |
| 거버넌스 | 미명시 | GDPR/PIPA right-to-delete, residency, audit_log, PII redaction |
| 단계 | 1주 dogfood | v1 dogfood (1주) → v2 first AX client (2-4주) → v3 GA |

## 1. Problem (확장된 버전)

```
TENANT A (HypeProof Lab — dogfood)
TENANT B (AX consulting client #1 — Phase 2)
TENANT C ... 
                                  │
   ┌──────────────────────────────┼──────────────────────────────┐
   │                              │                              │
   ▼                              ▼                              ▼
[Discord]  [Slack]  [Notion]  [Drive]  [GitHub]  [Calendar]  [Email]
[Confluence] [Jira] [Linear] [Zoom transcripts] [Fireflies] [Custom]
   │                              │                              │
   └──────────────────────────────┼──────────────────────────────┘
                                  ▼
                  [Collection AI Agent]
                   (per-source connector,
                    continuous incremental sync,
                    rate-limited, idempotent)
                                  │
                                  ▼
                          events (per-tenant)
                          모든 raw 보존, 임베딩 X
                                  │
                                  ▼
                       [Distill — 3 gates + LLM]
                        per-source strategy,
                        tenant-tunable prompts
                                  │
                                  ▼
                     artifacts + chunks (per-tenant)
                       refined + RAG-retrievable
                       version/supersede tracked
                                  │
                                  ▼
                  ┌───────────────┼───────────────┐
                  ▼               ▼               ▼
              member chat     analytics      external query
              (RBAC scoped)   (S0~S5, etc.)  (API, MCP)
```

핵심 약속:
- **모든 채널/문서/메시지를 빠뜨리지 않고 잡아낸다** (자료가 흩어진 회사에서
  signal이 어디 있을지 사전에 모름).
- **벌어들인 raw는 영구 보존** (재정제 가능). 비용 모델은 §3.
- **권한은 3계층** — Sediment(우리)·tenant 관리자(고객사 admin)·member.
- **벤더-락 없음** — connector는 표준 인터페이스. 고객사가 옮겨갈 때 export
  가능 (raw + refined 둘 다).

## 2. 3가지 architectural commitment

### 2.1 2-tier 저장 (v0.1과 동일, 재확인)

| Tier | 테이블 | 정책 |
|---|---|---|
| Raw 활동 | `events` | tenant 모든 capture **무조건** 적재, 임베딩 X. 영구 보존 (right-to-delete 제외). 비용 = text/jsonb 저장만. |
| 정제 지식 | `artifacts` + `chunks` | distill 통과한 것만, 임베딩 O. ref 기준 version 추적. RAG/검색 표면. |

→ **capture 시 필터링 안 함**: 사전 필터링은 "지금 noise라 생각한 게 미래의
signal일 수 있다"를 무시. raw 보존 + 재정제 옵션이 안전망.

### 2.2 Capture는 wide, filter는 narrow (in distill)

```
모든 채널·모든 문서·모든 메시지 ──► events (싸게, 무차별)
                                       │
                                       ▼
                                 distill 3-gate
                                       │
                                       ▼
                              signal만 artifacts
```

cost 통제는 distill 단에서: 
- Gate 2 (LLM 부르기 전 휴리스틱): ack/짧은 거 drop, 24h thread 묶기
- Gate 3 (LLM 출력 후 threshold): body 길이, confidence 등

raw 저장은 거의 무료 (Postgres jsonb). 임베딩이 비싼 자원이라 그쪽만 게이트.

### 2.3 3-layer RBAC

| 계층 | 누가 | 무엇을 제어 |
|---|---|---|
| **Platform** | Sediment ops (우리) | tenant 생성/정지, plan tier (Free/Pro/Enterprise), quota (events/mo, embeddings/mo, distill calls/mo), feature flags, kill switch |
| **Tenant** | 고객사 admin (예: 그 회사 CTO) | source enable/disable, connector config + secrets, member 초대·role 부여, 채널 sensitivity tag, retention policy override, residency |
| **Member** | 고객사 일반 사용자 | 본인 query, 본인이 본 artifact, 본인이 marked한 decision, 본인 정보 right-to-delete |

**Resource ACL on artifacts:**
- `artifacts.visibility` ∈ `{public, tenant, role-set, member-set}` — 누가 검색·인용 가능한지
- `artifacts.sensitivity` ∈ `{normal, confidential, restricted}` — 검색결과 자체에서 마스킹/숨김 여부
- channel/source-level inheritance: `#board-meetings`에서 온 거는 자동 `confidential` (tenant config)
- member-level subscription: member.subscribed_sources[] — 그 사람한테 보일 후보 좁힘

**Roles** (members.role 확장 enum):
- `superadmin` (Sediment ops only, tenant scope X)
- `tenant_owner` (고객사 최고권자, billing+settings)
- `tenant_admin` (member 관리, connector 관리)
- `creator` (디폴트 — 본인 쿼리/capture/mark)
- `viewer` (read-only, RAG 검색만)
- `external` (제한된 범위 — 컨설턴트·외부 협력자)

매 query는 tenant_id + member_id + role + visibility → SQL row-level security
(Postgres RLS 이미 init.sql에 있음, policy 확장).

## 3. Capture layer — Collection AI Agent

### 3.1 Connector ABC

```python
class Connector(Protocol):
    source: str                   # "discord" | "slack" | "notion" | ...
    tenant_id: str
    config: dict                  # tenant 설정 (allow-list 없음! everything; only credentials)
    cursor: dict | None           # last_message_id / last_edit_ts / sha / ...
    rate_limit: RateLimit         # source-side API 호출 제한 준수

    async def health() -> HealthStatus: ...
    async def fetch(since: datetime) -> AsyncIterator[Item]:
        """incremental, idempotent, respects cursor + rate_limit.
        야mits ALL accessible items in scope (no filtering)."""

    async def normalize(raw: Any) -> Item: ...
    
    def dedup_key(item: Item) -> str: ...  # source-natural id
```

핵심 원칙:
1. **Connector는 filter 안 함.** Bot/integration의 접근 권한 내에서 보이는
   모든 것을 가져온다. 필터링은 distill의 책임.
2. **incremental cursor.** 매 fetch는 since 이후만 (full re-scan은 별도 backfill).
3. **rate limit 준수.** Discord/Slack/Notion 각자 quota — `tenacity`-style
   backoff + per-source RPS.
4. **idempotent.** dedup_key로 중복 차단 (이미 ingester가 그러지만 connector
   단에서도 한 번 더 = network 절약).
5. **normalize는 lossless.** payload jsonb에 원본 통째로 저장 → 미래에 다른
   필드 필요해도 재정제 가능.

### 3.2 Connector catalog (enterprise SaaS)

```
[Communication]                  [Code]                   [Calendar/Meetings]
  discord (v1 dogfood)            github (v1, GH Action)   google_calendar
  slack                           gitlab                   outlook_calendar
  ms_teams                        bitbucket                zoom (recordings + transcripts)
  telegram                                                 fireflies
  email_imap                     [Docs/Wiki]               otter
                                  notion (v2 first AX)     gemini_meeting_notes
[Tasks/Project]                   confluence                
  jira                            google_docs             [Storage]
  asana                           office365                google_drive
  linear                          quip                     dropbox
  trello                                                   box
  notion_db                      [CRM/Support]             s3 (assumed-role)
                                  hubspot                  sharepoint
[Custom]                          salesforce
  webhook_receive (any source     intercom
   that can POST to /webhook/)    zendesk
```

v1: `discord` + `manual` + `github` (재정리). 다른 거는 첫 AX 고객 잡고 그
회사 스택에 맞춰 우선순위.

### 3.3 Discord connector — v1 detail

**모든 채널 전수집 모드** (Jay 결정):
- Bot 권한: `View Channels` + `Read Messages` + `Read Message History` (writes 권한 X — read-only)
- Server-wide role로 부여 → 새 채널 추가돼도 자동 capture
- **Channel allow-list 없음.** `#잡담`도 events에 들어감. distill이 알아서 거름.
- Per-message events.payload에 channel/author/content/ts/attachments/reactions/thread_id 다 보존
- `#잡담`처럼 시그널 거의 0인 채널은 tenant config에 `low_priority_channels[]` 표시 → distill Gate 2가 그것만 다른 휴리스틱 적용 (예: ≥3 reaction 받은 메시지만 transcript에 포함). 그래도 raw는 keep.

**v1 mechanism: REST polling + Gateway (v2)**:
- v1: Discord REST API + cursor (last message_id per channel) + every 2-5min poll. 채널 수 × 호출 = quota 친화.
- v2: Discord Gateway (WebSocket) → 실시간. 비싼 거 아님, 운영 복잡도 살짝 ↑.

**bot 권한 / 보안:**
- 토큰은 Fly secret (`DISCORD_BOT_TOKEN_<tenant_slug>`) 또는 미래 KMS reference
- 토큰 회전 가능 (tenant_admin UI에서 rotate)
- 멤버 권한과 무관 — bot은 *모든 채널 raw 수집*; 멤버 query 시점에 RBAC로 필터

### 3.4 멀티테넌트 config 스키마

```sql
CREATE TABLE tenant_connectors (
  tenant_id     UUID REFERENCES tenants(id) ON DELETE CASCADE,
  source        TEXT NOT NULL,                  -- "discord" | "slack" | ...
  enabled       BOOLEAN DEFAULT true,
  config        JSONB NOT NULL DEFAULT '{}',    -- source-specific (no secrets)
  secret_ref    TEXT,                           -- KMS path; never the secret itself
  cursor        JSONB DEFAULT '{}',             -- per-source progress
  last_run_ts   TIMESTAMPTZ,
  last_error    TEXT,
  health        TEXT DEFAULT 'unknown' CHECK (health IN ('ok','degraded','failed','unknown')),
  created_at    TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (tenant_id, source)
);

CREATE TABLE connector_runs (
  id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id     UUID NOT NULL,
  source        TEXT NOT NULL,
  started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  ended_at      TIMESTAMPTZ,
  items_fetched INT DEFAULT 0,
  items_ingested INT DEFAULT 0,
  status        TEXT,                           -- "running"|"ok"|"failed"|"rate_limited"
  error         TEXT
);
```

connector 운영은 observable해야 함 — runs 테이블이 SRE/디버깅 표면. 

### 3.5 워커 풀 / 호스팅

```
                    ┌─ DiscordWorker (HypeProof tenant)
Scheduler ──poll──► ├─ NotionWorker (AX client A tenant)
(per-minute)        ├─ SlackWorker  (AX client B tenant)
                    └─ ...
                              │
                              ▼  POST + HMAC
                       Sediment /webhook/<source>
```

- v1: Fly machine 안 supervisord에 추가 프로세스 1개 (모든 tenant·모든 source
  → 같은 worker 안에서 async concurrent). Concurrency budget 작아도 dogfood
  enough.
- v2 (첫 AX 고객 후): tenant 격리가 필요해지면 → tenant 별로 Fly Machines
  분리 또는 dedicated worker process. data residency (EU 고객은 EU 머신).
- v3 (GA): Kubernetes / Fly Machines auto-scale. tenant 격리는 product
  decision (격리 비용 ↑ vs shared 비용 ↓).

### 3.6 비용 모델 (per-tenant)

| 항목 | unit cost (대략) | 1000 events/day tenant | 100k events/day tenant |
|---|---|---|---|
| events 저장 (Postgres jsonb avg 2KB) | ~$0/GB-month (Supabase Pro 8GB 포함) | 60MB/mo | 6GB/mo |
| distill LLM (Anthropic Haiku) | ~$0.001/transcript | ~$3/mo | ~$300/mo |
| embedding (OpenAI 3-small) | ~$0.00002/chunk | ~$0.2/mo | ~$20/mo |
| Fly compute (worker proc) | 공유 시 ~$0 | 공유 | 격리 시 ~$10/mo |
| **합계 (대략)** | | **~$5/mo** | **~$330/mo** |

→ Pricing tier:
- Free (Lab dogfood-style): 1k events/day, 1 connector, $0
- Pro: 10k events/day, 3 connectors, ~$50/mo
- Enterprise: unlimited + tenant isolation + residency, $1000+/mo (커스텀)

이건 대략 — 실제 마진은 distill prompt 효율 + 임베딩 알고리즘 선택에 크게 좌우.

## 4. RBAC 모델 디테일

### 4.1 Tenant + Member + Role + Resource

```
tenant            (UUID, slug, plan, region)
  └ members       (tenant_id, user_id, role)
                     └ role ∈ {superadmin, tenant_owner, tenant_admin,
                                creator, viewer, external}

artifacts        (tenant_id, ref, type, body, ...)
  + visibility   ∈ {public_within_tenant, role:tenant_admin+, member_set:[...]}
  + sensitivity  ∈ {normal, confidential, restricted}
  + source       (어느 connector에서 왔나) ─► tenant_connectors.config.default_sensitivity
```

**Postgres RLS policy 예시 (이미 init.sql에 일부 있음, 확장):**

```sql
-- artifacts: 본 tenant만 + visibility/sensitivity 필터
CREATE POLICY artifacts_member_visibility ON artifacts FOR SELECT
USING (
  tenant_id = current_tenant_id()
  AND (
    visibility = 'public_within_tenant'
    OR (visibility = 'role:tenant_admin+' 
        AND current_member_role() IN ('tenant_owner','tenant_admin','superadmin'))
    OR (visibility = 'member_set' 
        AND current_member_id() = ANY (visible_to_member_ids))
  )
  AND (
    sensitivity = 'normal'
    OR (sensitivity = 'confidential' AND current_member_role() != 'external')
    OR (sensitivity = 'restricted' AND current_member_role() IN ('tenant_owner','tenant_admin'))
  )
);
```

chunk-level은 artifact 따라감 (chunk JOIN artifact 시점에 위 정책 적용).

### 4.2 Platform RBAC (우리)

- `superadmin`은 tenant_id = NULL — cross-tenant view 가능 (운영용)
- audit_log에 모든 superadmin 행동 기록 (kill switch, quota 변경 등)
- platform quota: `tenant_quotas` 테이블
  ```sql
  CREATE TABLE tenant_quotas (
    tenant_id UUID PRIMARY KEY REFERENCES tenants(id),
    events_per_month_limit BIGINT,
    distill_calls_per_month_limit INT,
    embedding_tokens_per_month_limit BIGINT,
    feature_flags JSONB DEFAULT '{}'
  );
  ```
  capture/distill worker는 매 호출 전 quota 체크 → 초과 시 skip + alert.

### 4.3 Sensitivity inheritance

기본은 `tenant_connectors.config.default_sensitivity` (예: `#board-meetings`
채널은 자동 `restricted`). distill artifact는 source의 sensitivity 상속.
멤버가 명시적으로 unmark 가능 (audit_log에 기록).

## 5. Distill — per-source strategy

### 5.1 같은 LLM, 다른 prompt + tool schema

source 종류마다 추출할 게 다르다:
- **meeting transcript** (Gemini meeting notes / Otter / Fireflies):
  decisions, action items, attendees, key questions
- **chat thread** (Slack/Discord):
  decisions, hand-offs, sentiment shifts
- **doc edit** (Notion/Confluence/Drive):
  decision (제목), rationale (body), reviewers, status
- **issue/PR** (GitHub/Jira/Linear):
  decision (resolution), reasoning, owner, blockers
- **email thread**:
  decisions, commitments, deadlines

→ distill 모듈은 `DistillStrategy` ABC, source별 구현체:

```python
class DistillStrategy(Protocol):
    source: str
    extractor_tool: dict     # Anthropic tool schema
    system_prompt: str
    transcript_builder: Callable[[list[Item]], str]
    confidence_threshold: float
```

기본 strategy (`consolidate_memory` decisions/actions)는 default. v2부터 source별
override.

### 5.2 3 gate (v0.1과 동일, 재확인)

Gate 1: source 자체 (capture는 wide니까 source-level enable/disable만)  
Gate 2: 비용 (LLM 부르기 전 — 짧은 거, ack drop, 묶기)  
Gate 3: signal (LLM 출력 후 — body 길이, confidence)

각 tenant가 Gate 2/3 threshold 튜닝 가능 (`tenant_connectors.config.distill_thresholds`).

### 5.3 Tenant-tunable prompts

기본 prompt + tool은 우리(Sediment)가 관리. 고급 tenant는 system prompt를
*append* 가능 (override는 안 — 우리 안전망):

```yaml
# tenant_connectors.config.distill_prompt_addendum
"""
이 회사는 SaaS 빌더야. 결정 추출 시 다음 항목에 특히 주의:
- pricing 결정
- security tradeoff
- customer commitment
"""
```

→ 시스템 프롬프트 끝에 추가됨. base 룰(do not invent 등)은 우리만 변경.

## 6. vault-differ (v0.1과 동일 — UPDATE / version 정책)

§6 of v0.1 그대로. 다만 추가:
- **per-tenant 정책 옵션**: 한 회사는 "히스토리 영구 보존" 원하고 (regulated), 다른
  회사는 "최신만 보고 옛 거 자동 archive 30일 후" 원할 수 있음. 
- `tenant_connectors.config.versioning_policy` ∈ {keep_all, archive_after_30d, replace_in_place}

## 7. Governance — audit, residency, deletion

### 7.1 audit_log (이미 init.sql에 있음)

확장: 모든 mutation (decision 생성·수정·삭제, member 추가, source 활성/비활성,
artifact sensitivity 변경) 기록. tenant_owner 가 audit_log 열람 가능.

### 7.2 Right to delete (GDPR/PIPA)

- Member 삭제 요청: 
  - `members.deleted_at` set, anonymize display_name/email
  - 그 member의 events: `payload`에서 personal_data 필드 redact (전체 삭제 X — analytics 보존), `member_id` NULL
  - 그 member가 생성한 decisions/artifacts: 본인 attribution만 제거, 콘텐츠는 tenant 자산이라 유지
- Tenant 삭제: 전체 cascade (events/artifacts/chunks/decisions/...)
- 모두 audit_log에 기록

### 7.3 Data residency

`tenants.region` ∈ {us-east, ap-northeast-1, ap-southeast-1, eu-west-1}.
Phase 3에서 Fly Machines per-region 배포. 그 전엔 single-region (Singapore).

### 7.4 PII detection (옵션)

distill 전에 PII detector pass (전화/주민/카드 번호 패턴 → 마스킹). v3 옵션 기능.

## 8. SaaS commercial layer (참고 — 별도 문서 필요)

Pricing tier × feature 매핑 일부:

| Feature | Free | Pro | Enterprise |
|---|---|---|---|
| Connectors | 1 (Discord) | 3 | 무제한 |
| Events/mo | 30k | 300k | 무제한 |
| Distill calls/mo | 1k | 10k | 무제한 |
| Members | 8 | 50 | 무제한 |
| RBAC custom | basic | full | full + audit export |
| Residency 선택 | X | X | O |
| Tenant isolation | shared | shared | dedicated |
| SLA | none | 99.5% | 99.9% |

상세는 DECISIONS.md §11.2 기준 + 별도 commercial doc.

## 9. 단계별 구현 (revised)

### v1 — Dogfood (이번 주, 1-2일)
1. schema: `tenant_connectors`, `connector_runs`
2. schema: artifacts/decisions에 visibility/sensitivity/version 컬럼
3. `services/collector/connectors/base.py` (ABC)
4. `services/collector/connectors/discord.py` — **모든 채널 전수집** (allow-list 없음)
5. Fly supervisord에 collector worker + distill scheduler 추가
6. distill.py: Gate 2 휴리스틱, Gate 3 threshold, per-source strategy 기반
7. vault-differ UPDATE/version 정식
8. Discord bot 생성 (Jay 1회) + token Fly secret

### v1.5 — HITL (다음 주)
9. Recent decisions 패널 (frontend) — Confirm/Reject/Edit
10. 📌 manual capture button + connector

### v2 — First AX client (2-4주)
11. Connector ABC 정식화 + Notion connector 추가
12. tenant 관리 admin UI (connectors enable/disable, secrets, member 초대)
13. RBAC 풀 적용 (RLS policy 확장 + role enum 확장)
14. audit_log 확장 + tenant_owner 열람 UI
15. 비용/quota 측정 + tenant_quotas

### v3 — Multi-source GA (분기)
16. Slack/Drive/Calendar/Jira connectors
17. Discord Gateway (WebSocket) — 실시간
18. Per-source distill strategy
19. Per-region 호스팅 (data residency)
20. PII redaction (옵션 기능)

## 10. Open Decisions — Jay

전부 build 전 결정 필요. v0.1보다 늘었음.

1. **Discord bot 권한 범위** — 서버 admin role 부여 OK? (모든 채널 자동 가시화)
   대안: 특정 role + 채널별 수동 invite (deny-by-default — 더 보수적). 권장: admin role.
2. **Discord bot token** — HypeProof Lab Discord에 새 bot 추가. Jay 1번. `~/.env`.
3. **Sensitivity 디폴트** — 새 채널 capture 시 기본 `normal`로 둘지 vs `tenant`로
   둘지. 디폴트 닫는 게 안전 (다른 회사 적용 시).
4. **Member role 확장** — 위 6단계(`superadmin`/`tenant_owner`/...) OK? 더 세분화?
5. **HITL UX 출시 타이밍** — v1.5에 넣을지 v2까지 미룰지. 권장: v1.5 (자동 distill
   결과 검토 안 하면 vault 신뢰도 ↓).
6. **데이터 보유 정책 디폴트** — events 영구 vs 1년. tenant override 가능하게 할지.
7. **Pricing tier 발표 시점** — 외부 공개 전엔 commercial doc 작성 안 해도 됨; 
   v2에서 첫 AX 고객 잡을 때 함께. 지금 결정 안 해도 됨.
8. **Connector 우선순위** — v2에서 첫 외부 고객 잡으면 그 회사 스택에 맞춰
   동적으로. 우선순위 사전 결정 의미 없음.

## 11. 호스팅 / 운영 / 모니터링

- collector worker: Fly supervisord 안 (v1), 별도 Machines (v2+)
- `connector_runs` 테이블이 health 표면 — Admin UI에서 보여줌
- alert: `last_run_ts` > 1h ago OR `status = failed` → Discord webhook 알림 (Mother 경유)
- 토큰 회전: tenant_admin UI에서 rotate (KMS reference만 바꿈)

## 12. v0.1 → v0.2 마이그레이션 (이미 코드된 부분)

- `distill.py` GOOD_DISCORD_CHANNELS allow-list → `tenant_connectors.config`로 이동
- 기존 `/webhook/discord-ingest`은 단일 endpoint 유지, payload `messages[]`에 모든 채널 그대로 들어옴
- 기존 `events.source='discord'` 그대로 (kind는 'message'/'reaction'/'thread'/'attachment' 등 확장)
- 신규 RLS policy는 기존 init.sql policy를 superseding하지 않고 *추가* — backward compat

## 13. 미해결 / 깊은 고민 필요

- **외부 connector의 PII**: Slack/email/calendar로 들어오는 데 회사 내부 민감
  정보 (인사·재무·법무) 어떻게 자동 분류? PII detection만으론 부족.
- **검색 권한 누설**: artifact가 RAG 인용에 들어가면 *제목/일부 내용*이 노출.
  query 자체에 RLS는 적용되지만, member가 자기에게 보이는 것만 인용에서도
  봐야 함 — chunk-level RLS 강제 필수. 빠뜨리면 데이터 누설.
- **Distill의 학습 데이터화**: 우리 LLM 호출이 tenant 데이터로 모델 학습되지
  않도록 Anthropic OPT-OUT 설정. 계약 조항 명시.
- **Cross-tenant 누설 사고 응답**: 어떤 메커니즘?

---

*v0.2 — 2026-05-20. Author: Claude. v0.1 (`23f961b`) superseded. 검토 대기: Jay.*
