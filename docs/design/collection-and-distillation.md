# Collection & Distillation — design (v0.1)

> 2026-05-20. Status: **design draft**, not implemented as a whole. Pieces
> already shipped flagged ✓; design-only flagged ✎. Authoritative consolidation
> of `ACTIVATION_ENGINE.md` Diagram 3 + `docs/dogfood/internal-loop.md` +
> `docs/dogfood/discord-ingest-mother-contract.md` + ad-hoc Slack/voice notes.
> Built so the dogfood loop closes AND the same surface works for external
> AX-consulting tenants (the moat).

## 0. Why this doc exists

Earlier today we closed three big gates (Anthropic key, Supabase/pgvector,
custom DNS). The **next thing dogfood depends on** is *automatic feeding* —
right now only `vault-ingest.yml` (GitHub push) fires automatically; every
other source (Discord, member chat, manual marks) is dormant or fictional.

Jay's instinct: "Collection Agent를 만들어야 할 것 같아." Correct. And the
two real questions before code are:

1. **Do we store everything, or only signal?** → §3.
2. **How do we distill meaning from noise?** → §5.

This doc answers both, names the open decisions, and orders the build.

## 1. Problem

```
Studio user ─┐
Discord ─────┤             ?                   ┌── 빠른 답 (RAG)
GitHub push ─┼── capture ─┼──────────► vault ──┼── "왜 결정했어?" (인용)
Calendar ────┤            ?                    └── 16-Essence 평가 / 분석
Manual 📌 ───┘
```

"?" 자리에 있어야 할 것: **(a) 모든 raw 활동을 잃지 않고 적재**, **(b) 그중
"지식이라 부를 만한 것"만 정제·인용 가능한 RAG 자산으로 승격**. 두 일이
같은 통에 들어가면 RAG가 잡담에 오염되고, 분리하지 않으면 분석을 못 함.

## 2. 이미 있는 것 (반영 / 재사용)

| 표면 | 상태 | 위치 |
|---|---|---|
| `events` 테이블 (tenant_id/source/kind/member_id/payload/ts) | ✓ | `infra/init.sql` |
| `artifacts` + `chunks` (pgvector 1536d + HNSW + tsv GIN) | ✓ Supabase 라이브 | `infra/init.sql` |
| `decisions`, `actions` 테이블 + `source_artifact_id` 링크 | ✓ | `infra/init.sql` |
| `vault-ingest.yml` (GitHub push → `/webhook/ingest`) | ✓ 자동 동작 | `infra/github-actions/`, hypeprooflab `.github/workflows/` |
| `/webhook/discord-ingest` (HMAC-서명 수신, id/fingerprint dedup) | ✓ Sediment 측 | `applications/vault_ingester/main.py` |
| `lab_lib/vault_paths.py` (type 감지, 제외 규칙) | ✓ | 그곳 |
| `distill.py` "정리" 에이전트 (Anthropic tool-use → decisions/actions → artifact landing) | ✓ 코드 + 라이브 키 | `scripts/distill.py` |
| `consolidate_memory.py` (Phase 4 L2 loop) | ✓ 코드, 스케줄 X | `scripts/` |
| Mother → `/webhook/discord-ingest` Mother-side fetch | ✎ 계약만 있음 | `docs/dogfood/discord-ingest-mother-contract.md` |
| **Collection Agent 자체 (Connector ABC, 멀티 소스, 멀티테넌트)** | ✎ 미설계 → **이 문서** | — |
| HITL reject/edit 흐름 | ✎ 미설계 | — |
| 외부 tenant 온보딩 흐름 | ✎ 미설계 (SaaS path) | — |

## 3. 저장 모델 — **2-tier**

이게 가장 중요한 결정. 두 통은 서로 다른 일을 한다.

```
        raw 활동 (전부 보존)              정제 지식 (signal만)
   ┌─────────────────────────┐    ┌────────────────────────────┐
   │ events                  │    │ artifacts + chunks         │
   │   tenant_id/source/kind │    │   pgvector(1536) + HNSW    │
   │   payload (jsonb)/ts    │    │   tsv GIN (hybrid BM25)    │
   │   text, 임베딩 X         │ ─► │   type='decision' 포함     │
   └─────────────────────────┘    └────────────────────────────┘
   비용: 매우 싸 (text+jsonb)    비용: 임베딩 호출 + 인덱스 메모리
   소비: 분석/freshness/S0~S5    소비: RAG 답변·인용·외부 데모
   삭제 정책: 절대 X (재정제 가능) 삭제: ref 기준 update/version
```

**룰:**
- **모든 캡처 → events 무조건 적재.** Discord 메시지, member query, cite_export,
  manual 📌, GitHub push 메타 — 전부. 추후 distill 알고리즘 바뀌면 *재정제* 가능.
- **artifacts/chunks는 distill 게이트를 통과한 것만.** 임베딩 비용 + 노이즈
  오염 두 문제 동시에 해결.
- `events.payload`는 jsonb이라 source-specific shape 자유. 표준화는 distill
  쪽이 책임.

**왜 이게 옳은지 검증:** 5/18 회의 결정(Cursor 비유: studio는 도구, 해자는
"남의 정제 SaaS"). raw 보존이 있어야 강의용 패턴 데이터(연료)도 살고,
정제만 vault에 가야 RAG 품질이 무너지지 않는다. 두 목적은 같은 통에서
못 산다.

## 4. Capture — Collection Agent

### 4.1 Connector ABC

```python
class Connector(Protocol):
    name: str                     # "discord", "github", "notion", "manual"
    tenant_id: str                # which org's data
    schedule: str                 # "*/5 * * * *" 또는 "on_demand"
    
    async def fetch(since: datetime, cursor: dict | None) -> tuple[list[Item], dict]:
        """returns (items, new_cursor). idempotent w.r.t. cursor."""

    def dedup_key(item: Item) -> str:
        """unique within (tenant_id, source) for dedup at ingester."""
```

각 Connector는 자기 소스 auth/액세스만 안다. Sediment에 들어가는 형식은
일관:

```python
class Item:
    source: str                   # "discord" | "github" | "notion" | ...
    kind: str                     # "message" | "doc" | "event" | "decision-mark"
    channel: str | None           # source-specific group (#meeting-notes, repo path, db page)
    author_external_id: str | None
    author_name: str | None
    content: str
    ts: datetime
    payload: dict                 # 완전 raw (loss-less)
```

### 4.2 v1 Connectors (dogfood)

**Priority 1 — `discord`** (HypeProof Lab 서버 기준, Jay 지정):

| Channel | Tier | Why |
|---|---|---|
| `#meeting-notes` (HYPEPROOF HQ) | **S** — primary signal | Gemini 미팅노트 자동 정리 → 결정 명시적 |
| `#hypeproof-studio` (HYPEPROOF HQ) | A | 제품 결정 / 개발 conversation |
| `#sediment` (HYPEPROOF HQ) | A | 제품 자체 dogfood |
| `#manager-공지사항` | B | 공식 발표 |
| `#rule` | B | 규범 |
| `#ai-leadership` (Edu) | B | 강의 / 학습 |
| `#잡담` 또는 chat | DROP | 노이즈 |

→ **config-driven allow-list.** 현재 `distill.py`에 하드코딩된 4개 (`weekly` 등)
은 이전 plan 잔재 → tenant config로 옮긴다.

**Priority 2 — `manual`** (📌 capture 버튼): 멤버가 Chat 화면에서 한 메시지/한
인용을 "이건 결정" 명시. 모든 게이트 우회, 즉시 distill 후보로 승격.

**Priority 3 — `github`** (이미 GH Action으로 동작; Connector ABC에 맞춰 정리만).

### 4.3 멀티테넌트 config

`tenants.feature_flags` 또는 신설 `tenant_connectors` 테이블:

```sql
CREATE TABLE tenant_connectors (
  tenant_id     UUID REFERENCES tenants(id),
  source        TEXT,        -- "discord"|"notion"|...
  config        JSONB,       -- {bot_token_secret_ref, allow_channels:[...], schedule}
  enabled       BOOLEAN DEFAULT true,
  cursor        JSONB,       -- per-connector "어디까지 fetch했나"
  last_run_ts   TIMESTAMPTZ,
  PRIMARY KEY (tenant_id, source)
);
```

`config.bot_token_secret_ref`는 *직접 secret 저장 X* — Fly secret manager 또는
같은 등급 KMS에 위탁 (외부 tenant 데이터 격리 위해 필수).

### 4.4 스케줄 + cursor + idempotency

- **Fly machine 안 supervisord 스케줄러** (`*/5 * * * *` 정도)가 매 분
  enabled connector를 깨워서 `fetch(since=last_run_ts, cursor)` 호출.
- 결과 items → Sediment webhook 엔드포인트로 POST (배치, HMAC-서명).
- ingester는 이미 idempotent (id 또는 fingerprint dedup). 재시도 안전.
- cursor는 source 특성에 맞춰: Discord는 마지막 message id, GitHub는 SHA,
  Notion은 last_edited_time.

### 4.5 어디서 도는가

| 호스팅 | 장점 | 단점 |
|---|---|---|
| **Fly machine 안 supervisord 추가 프로세스** (권장) | 항상-on, Fly secret 공유, 추가 인프라 0 | Fly 머신 자원 약간 ↑ |
| 별도 Fly Machines scheduled task | scale 분리 | 추가 설정 + secret 복제 |
| Mother bot 안 (hypeprooflab) | "Mother가 Discord 담당" 룰 준수 | Mother 죽으면 멈춤; 외부 tenant 적용 불가 |

**권장: Fly machine 안.** "Mother가 Discord" 룰은 *송신* 룰 (모든 봇이
DM 보내면 충돌). *수신*은 충돌이 없고, 외부 tenant 확장도 자연. Mother는
계속 Discord post를 담당하고, Collection Agent는 read-only.

## 5. Distill — 3-gate 파이프라인

```
events / conversations
        │
        ▼
[Gate 1: Source allow-list]            ← tenant_connectors.config.allow_channels
        │   (#meeting-notes ✓, #잡담 ✗)
        ▼
[Gate 2: 비용 휴리스틱 — LLM 부르기 전]   ← NEW
        ├ 한 줄 미만 (≤30 char) drop
        ├ pure emoji/reaction drop  
        ├ "ㅋ" "ㅎ" "ack" 같은 ack drop
        ├ 24h 윈도우 / 채널·thread별로 묶어 transcript 형태로 통합 (토큰 ↓)
        └ manual 📌은 모든 Gate 우회
        │
        ▼
[LLM Distill — Anthropic tool-use]      ← 이미 _SYSTEM / _EXTRACT_TOOL 있음
   record_decisions_and_actions(transcript)
     → decisions: {topic, body(왜), status: open|made|reverted}
     → actions:   {description, owner_hint, due_date, decision_topic}
   prompt 룰: "do not invent — Q&A뿐이면 빈 배열 반환"
        │
        ▼
[Gate 3: signal threshold — vault 승격 전]  ← NEW
        ├ decision.body 길이 ≥ 50 char
        ├ topic 의미 있는 명사 어휘 ≥ 2 (휴리스틱)
        ├ 이미 known이면 update 분기로 (§6)
        └ skip 시 events에는 그대로 남음 (재정제 가능, §3)
        │
        ▼
[vault-differ] (§6 정식 정의)
        │
        ▼
artifacts(type='decision', citable) + chunks(embed) + decisions(linked)
```

### 5.1 비용 모델

- LLM (Anthropic Haiku, distill용): transcript 평균 ~500 tok in, ~300 tok out
  → ~$0.001/transcript. 일 100 transcript = $0.10/day = $3/mo. **저렴.**
- Embedding (OpenAI 3-small): chunk당 ~$0.00002. decision 1건 → 평균 3 chunk
  → $0.00006/decision. 일 20 decision = $0.0012/day. **무시 가능.**
- Gate 2가 noisy 메시지를 미리 거르면 위 estimate의 ~30%로 줄어듦.

### 5.2 신선도 SLO

| 단계 | 목표 | 측정 |
|---|---|---|
| Discord post → events row | < 7 min (5min cron + 처리) | `events.ts` vs Discord ts |
| events → distilled artifact | < 1 h (distill cron 1h 간격) | `vault.ingest` event + `decisions.created_at` |
| artifact → RAG 검색 가능 | 즉시 (insert 시 임베딩 동기 수행) | smoke query 빈도 |

## 6. vault-differ — new / update / known

ref 기준 idempotency만으론 부족 (같은 topic이 진화하는 경우 히스토리 손실).

**정식 정의:**

| 상태 | 트리거 | 행동 |
|---|---|---|
| **NEW** | `ref = decision/<slug>` 가 vault에 없음 | artifact INSERT + chunks INSERT + decisions INSERT |
| **UPDATE** | ref 존재 + new body가 옛 body와 ≥30% 의미 차이 (cosine on embeddings) | 옛 artifact `status='superseded'` + 새 ref `decision/<slug>-v<N+1>` INSERT + decisions에 새 row + 이전 decision row의 `replaced_by_decision_id` 세트 |
| **KNOWN** | ref 존재 + body 거의 동일 | no-op artifact-side, decisions에 새 conv_id link만 append (re-mention 추적) |

스키마 추가 필요:
```sql
ALTER TABLE decisions ADD COLUMN replaced_by_decision_id UUID REFERENCES decisions(id);
ALTER TABLE artifacts ADD COLUMN status TEXT DEFAULT 'published'
  CHECK (status IN ('published','superseded','draft','rejected'));
ALTER TABLE artifacts ADD COLUMN supersedes_artifact_id UUID REFERENCES artifacts(id);
```

UI 결과: "왜 X 결정했어?" 물으면 최신 version 인용 + 과거 version도 검색
가능 (timeline view).

## 7. HITL — 멤버 reject / edit / promote

자동 LLM extraction은 가끔 틀린다. 안전망:

- 각 distill 산출 artifact는 `confidence` 메타 (LLM tool 호출 시 self-score
  또는 휴리스틱 score).
- 멤버 UI에 `Recent decisions` 패널: 최근 24h 자동 생성 decision 카드 → 각자
  **✓ Confirm** / **✗ Reject** / **✏ Edit** 가능.
- **Reject**: artifact `status='rejected'` + decision row soft-deleted.
- **Edit**: 새 version 생성 (UPDATE 흐름과 동일).
- **Confirm**: status='published-confirmed' (search ranking에 boost).
- 30일 후 confirm/reject 없으면 자동 `published-stale` (랭킹 약간 ↓).

이 패턴은 외부 tenant에도 그대로 — "AI가 너희 데이터 정제해줄게, 단 너희가
승인" = 신뢰 빌딩.

## 8. 외부 tenant 온보딩 (SaaS path)

1. 새 `tenants` row + `tenant_connectors` rows.
2. Connector별 OAuth 또는 토큰 입력 (Notion: integration token, Slack: bot token,
   Drive: OAuth). KMS에 저장, `tenant_connectors.config.secret_ref` 참조.
3. 첫 backfill: connector가 since=epoch 부터 fetch (bounded by config.max_backfill).
4. distill cron 자동 가동. 일주일 후 HITL 패널로 confirm/reject 일괄 검토.
5. Acceptance: tenant 멤버 ≥1명이 "왜 ... 결정?" 질문에 자기 회사 자료 인용
   답을 받음. = "재정제됨" 증거.

## 9. v1 구현 순서 (dogfood-우선, 1주 분량)

```
1.  [Schema] decisions.replaced_by_decision_id + artifacts.status/supersedes
2.  [Schema] tenant_connectors 테이블
3.  [Code]  connectors/base.py (ABC)
4.  [Code]  connectors/discord.py (#meeting-notes + #hypeproof-studio + #sediment)
5.  [Code]  shipper.py (HMAC + batch + retry; 이미 vault-ingest workflow가 답습)
6.  [Code]  vault-differ 정식: scripts/distill.py 의 ref upsert → §6 흐름으로
7.  [Code]  Gate 2 비용 휴리스틱 + Gate 3 threshold (distill.py)
8.  [Infra] Fly supervisord에 collector + distill 두 cron 추가
9.  [UI]    Recent decisions panel + Confirm/Reject (HITL)
10. [Token] HypeProof Discord bot 생성 (Jay 1회) → fly secrets set
```

Phase 2 (외부 tenant 준비, 1~2주):
- Notion/Slack connectors
- per-tenant config UI (Admin 페이지)
- KMS secret integration

## 10. Open Decisions — Jay 결정 필요

1. **Discord 채널 allow-list 확정** — 이 문서 §4.2 표 OK? `#manager-공지사항`/
   `#rule` 같은 공식 announcement도 vault에 갈지 (B tier로 넣었음).
2. **Discord bot token 소유** — HypeProof Lab Discord에 새 bot 추가, role/권한
   (read messages + read message history). Jay가 1번 만들고 `~/.env`에 추가.
3. **Collector 호스팅 결정** — §4.5 권장 (Fly machine 안 supervisord) OK?
   Mother로 보내고 싶은 강한 이유 있나?
4. **HITL UX** — Recent decisions confirm/reject 패널 만들 가치 있나, 아니면
   처음엔 confidence threshold만 높여서 자동 publish?
5. **만약 LLM 추출이 *0건* 반환** — 그 transcript는 events에 그대로 남기되,
   재정제 시도 안 함 (현 정직 모드) vs 다른 모델로 retry? **권장: 그대로**
   (do not invent 원칙).
6. **데이터 보유 (events) 정책** — 영구? 90일? 1년? GDPR/PIPA 들어오기 전엔
   영구가 분석에 유리하지만, 멤버 탈퇴 시 삭제권 보장 의무.

## 11. 이 문서 자체

- 위치: `docs/design/collection-and-distillation.md`
- 자동 ingest 대상 (`docs/**/*.md` 패스에 들어 있음) → 이 문서를 머지하면
  Sediment vault에 들어가서 본인이 본인 설계 검색 가능. 메타 ✨
- 다음 작업: §10 결정 확정 → §9 구현 순서대로 build.

---

*v0.1 — 2026-05-20. 작성: Claude. 검토 대기: Jay.*
