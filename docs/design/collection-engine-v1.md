# Collection Engine v1.0 — Architecture Spec

**Status:** Draft 2026-05-21 PM. The unified engine architecture for Sediment SaaS.
**Supersedes:** `collection-and-distillation.md` v0.3 (kept as historical reference)
**Driven by:** [ICP-segmentation.md v1.1](./ICP-segmentation.md) — PIPA-clean, voice/OCR P1, KakaoTalk auto-fetch NEVER
**Sub-specs:** [voice-ocr-connector-spec.md](./voice-ocr-connector-spec.md), [pricing-strategy memo (internal)](../../services/sediment/prompts/...)

> **TL;DR:** Sediment is a **4-layer evidence-grounded knowledge engine** for SMBs without data discipline. Capture (PIPA-clean inputs only) → Distill (LLM extraction with safety guards) → Govern (auto-redact, archive, anomaly) → Serve (cited RAG answers). 18-month phased build from current single-tenant prototype to ~50-tenant SaaS supporting D/A/B/C archetypes. Single-VM Y1, Celery+Postgres queues Y2 when 50+ tenants. PIPA only — SOC 2 / GDPR / HIPAA out of scope until 2027.

---

## 0. North Star

**Sediment is the first data layer for companies that never had one.**

- We serve SMBs (5-100 people) whose institutional knowledge lives in **owner's head + 카톡 + 종이 + meeting drift**.
- We don't compete with Glean (we serve people who don't know Glean exists).
- We don't replace meeting tools (Otter/Fireflies do one slice — we cover voice + meetings + photos + chat).
- We do one job well: **turn ephemeral team comms into evidence-grounded, citable, queryable knowledge** — without ever taking patient/customer data without explicit consent.

**Y1 anchor**: 30-50 paying tenants across A (치과) + D (consulting alumni). Per-tenant LLM cost ≤ $5/mo. Studio price ₩99K. 95% margin.

---

## 1. The 4-Layer Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  SERVE        (UI / API / RAG chat / citations / library browser)    │
│               ───────────────────────────────────────────────────    │
│               artifacts + chunks (vector + BM25) → cited answers     │
└─────────────────────────────────────────────────────────────────────┘
                              ▲
                              │ vault writes
                              │
┌─────────────────────────────────────────────────────────────────────┐
│  GOVERN       (auto-redact / archive / anomaly / consent enforce)    │
│               ───────────────────────────────────────────────────    │
│               propose-only LLM + execute policies + PIPA audit       │
└─────────────────────────────────────────────────────────────────────┘
                              ▲
                              │ artifacts after gating
                              │
┌─────────────────────────────────────────────────────────────────────┐
│  DISTILL      (strategy routing / confidence gate / cost-aware)      │
│               ───────────────────────────────────────────────────    │
│               events → decisions/actions/artifacts via Anthropic     │
└─────────────────────────────────────────────────────────────────────┘
                              ▲
                              │ NormalizedEvent stream
                              │
┌─────────────────────────────────────────────────────────────────────┐
│  CAPTURE      (Connector framework — PIPA-clean sources only)        │
│               ───────────────────────────────────────────────────    │
│               Voice 🎤 | Photo 📷 | Slack/Discord | Notion | …       │
└─────────────────────────────────────────────────────────────────────┘
```

### Cross-cutting concerns (apply to every layer)

1. **Multi-tenant isolation** — tenant_id RLS at DB, JWT scope at API, RBAC (3-layer) at UI
2. **Cost / billing alignment** — per-tenant metering, quota enforcement, overage alerting
3. **Observability** — per-tenant metrics, per-connector health, per-strategy precision tracking
4. **Security & compliance (PIPA)** — encryption, audit log, consent records, DSR (data subject rights)

---

## 2. Capture Layer

### 2.1 Connector framework (`lab_lib/connectors/`)

```python
class ConnectorABC(abc.ABC):
    source_name: str

    async def list_resources(self) -> list[Resource]:    # channels / spaces / repos
    async def fetch_since(self, resource, watermark, limit) -> list[NormalizedEvent]
    async def aclose(self) -> None
```

All connectors emit **NormalizedEvent** — a single shape consumed by Distill:

```python
@dataclass
class NormalizedEvent:
    source: str           # "discord" | "slack" | "voice" | "ocr" | "notion" | ...
    kind: str             # "message" | "voice_memo" | "meeting_transcript" | "paper_minutes" | "page_edit"
    external_id: str      # source-native unique id (dedup key)
    ts: datetime          # source timestamp UTC
    payload: dict         # raw + enrichment
    member_external_id: str | None
    resource_id: str | None
```

### 2.2 Connector catalog (post-PIPA pivot)

| Connector | Priority | Status | Phase | PIPA gating |
|---|---|---|---|---|
| **Voice (memo + meeting)** | **P1** | spec'd | A (4w) + B (3w) | BYOData / pre-meeting consent |
| **Photo OCR** | **P1** | spec'd | C (2w) | BYOData (user's paper) |
| **Discord** | P2 | ✅ MVP shipped | done | Workspace admin OAuth |
| **Slack** | P2 | not started | Q4 2026 | Workspace admin OAuth |
| **KakaoWork** | P2 | not started | Q4 2026 | Admin OAuth + 약관 우산 |
| **Notion** | P2 | not started | Q1 2027 | Admin OAuth |
| **Google Drive** | P3 | not started | Q1 2027 | OAuth + per-file consent |
| **Email (IMAP/Gmail)** | P3 | not started | Q2 2027 | Self-mailbox only |
| **GitHub** | P4 | not started | Q2 2027 | Admin OAuth |
| **Jira** | P4 | not started | Q2 2027 | Admin OAuth |
| **KakaoTalk export upload** | P3 | not started | Q1 2027 | User manual upload (BYOData) |
| **❌ KakaoTalk 일반 단톡방 auto-fetch** | **NEVER** | — | — | **PIPA violation, prohibited forever** |

### 2.3 Capture pipeline (high-level)

```
External source ─▶ Connector.fetch_since() ─▶ NormalizedEvent
                                                    │
                                                    ▼
                                            ┌──────────────────┐
                                            │ events table     │
                                            │ (tenant_id, src, │
                                            │  kind, payload,  │
                                            │  ts, external_id)│
                                            │ + uniq idx       │
                                            └──────────────────┘
                                                    │
                                                    ▼ (async)
                                            ┌──────────────────┐
                                            │ transcribe_jobs  │ (voice/photo only)
                                            │ queue            │
                                            └──────────────────┘
                                                    │
                                                    ▼
                                            Whisper / Claude vision
                                                    │
                                                    ▼
                                       update events.payload with transcript/ocr
```

### 2.4 Scheduling (current: APScheduler in-process; future: Celery)

| Job | Cadence | Phase 1 (now) | Phase 2 (50+ tenants) |
|---|---|---|---|
| Discord fetch | every 30 min per channel | APScheduler | Celery Beat → workers |
| Voice/OCR transcribe | every 5 min poll | APScheduler | Dedicated worker pool |
| Distill | hourly | APScheduler | Celery worker |
| Consolidate memory | every 12h | APScheduler | Celery worker |
| Health check | daily 06:00 KST | APScheduler | Celery + alert webhook |
| Cost monitor | daily 06:30 KST | APScheduler | Celery + alert webhook |
| Governance sweep | weekly | not yet wired | Celery worker (Phase B) |

**Migration trigger to Celery**: when single-VM APScheduler hits sustained > 5 jobs/sec or > 10 tenants with hourly distill cycles.

---

## 3. Distill Layer

### 3.1 Strategy framework (`services/sediment/prompts/`)

Already shipped:
```
prompts/
├── distill/
│   ├── base.yaml                       # default workhorse
│   └── strategies/
│       ├── chat_thread.yaml            # Discord/Slack/Teams
│       ├── meeting_transcript.yaml     # Gemini/Otter/Fireflies + our own meeting recordings
│       ├── doc_edit.yaml               # Notion/Confluence/Drive
│       └── code_change.yaml            # GitHub/GitLab PRs
└── governance/
    ├── base.yaml                       # 6 categories (archive/redact/cascade/promote/preserve/anomaly)
    └── strategies/
        ├── archive_stale.yaml
        ├── redact_pii.yaml
        └── anomaly_flag.yaml
```

Phase A additions (Q3 2026):
- `voice_dump.yaml` — single-speaker stream-of-consciousness
- `paper_minutes.yaml` — OCR-text from photographed minutes
- `sop_capture.yaml` — derive SOP from repeated patterns (A 치과 + C 무파이프 핵심)

Phase B additions (Q4 2026):
- `vendor_thread.yaml` — vendor negotiation history (B SMB)

### 3.2 Loader contract (already shipped)

```python
strat = load_strategy("distill", "voice_dump", tenant_id="<uuid>")
msgs = render_messages(strat, user_text=transcript)
resp = await client.messages.create(
    model=...,
    system=strat.system_prompt,
    tools=[strat.tool_schema],
    tool_choice={"type": "tool", "name": strat.tool_schema["name"]},
    messages=msgs,
)
```

Tenant override invariants (hard floors per loader code):
- `system_prompt`: base never overridable; tenant append-only addendum
- `tool_schema`: frozen (downstream parsers depend)
- `confidence_threshold`: tenant can lower but never below 0.5 (distill) / 0.55 (governance)
- `guards`: append-only
- "Do not invent" guard is unremovable

### 3.3 Distill execution flow (current + planned)

```
events ─▶ group by (channel, day) or (kind, source)
              │
              ▼
       per-group ─▶ load_strategy(by source/kind routing)
              │
              ▼
       _extract(messages, strategy=...) ─▶ Anthropic tool-use call
              │
              ▼
       confidence_threshold gate ─▶ drop low-conf decisions
              │
              ▼
       record_call() ─▶ llm_calls (real token + USD)
              │
              ▼
   decisions + actions + artifacts ─▶ vault (chunked + embedded)
```

### 3.4 Per-source strategy routing (current rules)

```python
# scripts/distill.py
def _strategy_for_source(source: str, kind: str, channel: str | None) -> str:
    if source == "discord":
        if channel and channel.lstrip("#").lower() in {"meeting-notes", "weekly"}:
            return "meeting_transcript"
        return "chat_thread"
    if source == "voice":
        return "voice_dump" if kind == "voice_memo" else "meeting_transcript"
    if source == "ocr":
        return "paper_minutes"
    if source == "slack":
        return "chat_thread"  # later: per-channel routing
    if source == "notion":
        return "doc_edit"
    if source == "github":
        return "code_change"
    return "chat_thread"  # safe default
```

---

## 4. Govern Layer

### 4.1 Six governance categories (from v0.3, unchanged)

| Category | Action | Phase status |
|---|---|---|
| `archive` | Move to cold storage (value degraded) | Phase B (Q4 2026) |
| `redact` | Replace PII spans with `[REDACTED]` placeholders | **Phase A escalated** (Q3 2026 — was propose-only) |
| `cascade_delete` | Delete orphaned children of reverted parent | Phase B |
| `promote_redistill` | Re-run distill on improved prompt | Phase C (Q1 2027) |
| `preserve` | Mark high-value artifact untouchable | Phase A |
| `anomaly_flag` | Flag suspicious content for review | Phase A (logging) → Phase B (Discord webhook alert) |

### 4.2 Govern execution model (rev 2 — PIPA-driven)

**Previously (v0.3 spec):** all governance = propose-only.

**Now (rev 2):** **2-tier governance**:

| Tier | What it does | Examples | Approval required |
|---|---|---|---|
| **Auto-execute** | Safety-critical, low-risk-of-error categories run immediately | redact_pii (auto-mask emails/names), credential_leak alert | No — fire on detection |
| **Propose-only** | Value-judgment categories require tenant_admin click | archive_stale, cascade_delete, promote_redistill | Yes — UI for admin |

Why: PIPA requires automatic PII protection — can't wait for admin click. Other categories preserve human control.

### 4.3 Consent enforcement (PIPA)

`consent_records` table (per voice-ocr-connector-spec §5.1) enforces:
- Pre-meeting recording requires all participants checked in
- Each meeting transcript artifact carries `payload.consent_record_id`
- Distill (`meeting_transcript` strategy) refuses to process if no consent_record_id
- Revocation API auto-redacts downstream artifacts

### 4.4 Audit log (`audit_log` table, new migration)

```sql
CREATE TABLE audit_log (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid REFERENCES tenants(id),
  actor_id        uuid REFERENCES members(id),  -- nullable for system actions
  action          text NOT NULL,                -- "read", "write", "redact", "delete"
  resource_kind   text NOT NULL,                -- "artifact", "event", "decision", "action"
  resource_id     uuid,
  ip_address      inet,
  user_agent      text,
  metadata        jsonb,                        -- action-specific context
  ts              timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_log_tenant_ts ON audit_log (tenant_id, ts DESC);
CREATE INDEX idx_audit_log_resource ON audit_log (resource_kind, resource_id);
```

Logged actions (PIPA requirement):
- Every artifact read by a member (who saw what when)
- Every PII auto-mask event
- Every export / DSR request
- Every consent collection + revocation
- Every distill batch execution (already in `llm_calls`)

---

## 5. Serve Layer

### 5.1 Current capabilities (shipped)

- `GET /api/v1/library` — vault browser (BM25 + filter by type)
- `GET /api/v1/library/search?q=...` — hybrid search (vector + BM25)
- `POST /v1/sediment/stream` — SSE chat with RAG citations
- `GET /api/v1/library/{ref:path}` — single artifact detail

### 5.2 Phase A additions (Q3 2026)

- Per-tenant prompt override (system_prompt addendum from `tenant_prompt_overrides` table — new migration)
- Citation links include playback (voice timestamp) / page navigation (OCR multi-page)
- Member-attribution display (decisions show responsible member with avatar)

### 5.3 Phase B additions (Q4 2026)

- DSR (Data Subject Rights) self-serve:
  - 열람 (read): GET /api/v1/me/data
  - 삭제 (delete): POST /api/v1/me/data/delete
  - 이의제기 (objection): POST /api/v1/me/data/object
- Tenant admin dashboard:
  - PII redactions executed (last 30 days)
  - Governance proposals pending approval
  - Cost dashboard (per-tenant)
  - Audit log viewer

### 5.4 Phase C additions (Q1 2027)

- Multi-language UI (KO + EN toggle)
- Mobile responsive deep-link from voice memo Discord notification
- "회사 매뉴얼" auto-generated playbook (C archetype hero feature)

---

## 6. Multi-Tenant Model

### 6.1 Tenant boundaries (current + planned)

```
Platform (Sediment ops — us)
  ↓ provides shared infra
Tenant (one customer org — e.g., "보아치과")
  ↓ has many
Member (one person — e.g., 원장 / 위생사 / 코디)
  ↓ has role
Role: admin | manager | member
```

| Layer | Isolation mechanism |
|---|---|
| **DB row** | tenant_id column + RLS policy per table |
| **API** | JWT scope `{tenant_id, member_id, role}` checked by `require_identity` middleware |
| **UI** | route prefix `/sediment/*` resolves to current_tenant via JWT |
| **Storage** | object keys prefixed `/<tenant_id>/...` in R2 |
| **Cost** | llm_calls.tenant_id + consent_records.tenant_id + audit_log.tenant_id all carry tenant_id |

### 6.2 RBAC (3-layer, simplified per ICP)

| Role | Korean equivalent | Capabilities |
|---|---|---|
| `admin` | 사장 / 원장 / 대표 | Everything; only admin can approve governance, change tier, manage members, see cost dashboard |
| `manager` | 팀장 / 코디 | Read all, write artifacts, run distill manually, see audit log |
| `member` | 직원 / 위생사 | Read artifacts + chat, write voice memos, request DSR |

Resource-level ACL (planned for Phase B):
- `artifacts.visibility ∈ {public, tenant, internal, private}` — column added
- `artifacts.sensitivity ∈ {low, medium, high}` — column added
- Per-resource overrides via `artifact_acl` join table

No SAML / SCIM until Q4 2027 (unnecessary for SMB / 치과 ICP).

### 6.3 Tenant provisioning flow

```
GitHub OAuth signup
    ↓
auth.py creates member row
    ↓
If member has no tenant: create new tenant (named "<user>'s workspace" by default)
    ↓
member is auto-admin of new tenant
    ↓
member can invite others via /sediment/admin/invites
    ↓
invited member joins as `member` role (admin can promote)
```

Future: tenant ownership transfer flow (Q1 2027) for cases where admin leaves the company.

---

## 7. Cost / Billing Alignment

### 7.1 Per-tenant cost lineage (already shipped)

`llm_calls` table records every Anthropic/OpenAI call with tenant_id, model, agent, strategy, tokens, USD. Daily rollup runs at 06:30 KST.

### 7.2 Quota enforcement (Phase A — Q3 2026, blocker for Free tier launch)

| Tier | Channels cap | Events/mo cap | Voice min/mo | Photo upload/mo | History days |
|---|---|---|---|---|---|
| Free | 1 | 100 | 30 min | 50 photos | 7 days |
| Solo | 3 | 1,000 | 200 min | 500 photos | unlimited |
| Studio | 10 | 10,000 | 500 min | 5K photos | unlimited |
| Pro | unlimited | 50,000 | 5,000 min | 50K photos | unlimited |
| Enterprise | unlimited | custom | custom | custom | unlimited |

Enforcement points (Phase A):
- Capture: connector rejects new event if monthly cap hit
- Transcribe: job queue rejects if monthly voice min exceeded
- UI: usage meter shows "60/100 events used" + upgrade nudge at 80%

### 7.3 Billing integration (Phase B — Q4 2026)

- Stripe Checkout for paid tiers
- Subscription created → tenant.tier upgraded → quotas raised
- Webhook handlers: created / updated / deleted / payment_failed
- 14-day trial → auto-convert (Studio/Pro)
- Annual prepay discount (1 month free)
- Cancellation flow: downgrade to Free if no payment in 7 days post-fail

### 7.4 Cost guardrails (Phase A — blocker)

- Per-tenant daily budget cap (default $5/day for Studio, $20 for Pro, $200 for Enterprise)
- Soft alert: 80% of daily cap → Discord webhook
- Hard cap: 120% → reject new distill calls for that tenant until reset

Cost-tracker already operational (commit `a152898`). Live measured: $0.06/day for HypeProof Lab — far below cap.

---

## 8. Compliance Roadmap

### 8.1 PIPA (한국 개인정보보호법) — REQUIRED before Q3 2026 external sales

Per [ICP §7](./ICP-segmentation.md):

| Requirement | Status | Phase |
|---|---|---|
| 위탁계약 (DPA) 표준 템플릿 | ❌ | Q3 2026 |
| 처리방침 공개 | ❌ | Q3 2026 |
| 암호화 at rest + in transit | ✅ | done (TLS + Supabase Pro) |
| 접근통제 (RBAC) | ✅ | done (JWT + RLS) |
| audit_log 테이블 | ❌ | Q3 2026 (migration) |
| PII 자동 마스킹 | 🟡 | Q3 2026 (propose → execute) |
| 보관기간 정책 | 🟡 | Q4 2026 |
| 침해 통지 절차 (72h) | ❌ | Q3 2026 (runbook) |
| DSR (열람/삭제/이의제기) | ❌ | Q4 2026 |

### 8.2 Out of scope (Q4 2027 reconsider)

- SOC 2 Type 1/2 (US enterprise sales prerequisite)
- GDPR (EU sales)
- HIPAA (US healthcare)
- ISO 27001

Reason: ICP is Korean SMB. Korean compliance is sufficient. Re-evaluate when first non-Korean lead appears.

### 8.3 의료법 (의료기관 ICP — A archetype)

Sediment intentionally **NEVER** touches:
- 진료기록 (별도 EMR 시스템 영역)
- 환자 카톡 1:1 대화
- 환자 SMS / 알림톡

By staying out of "진료기록"으로서의 환자 데이터, 우리는 의료법 26조 (진료기록 보호의무)의 직접 대상이 아님. 운영 데이터만 처리.

영업 자료에 명시: "환자 데이터는 절대 안 만집니다. EMR 영역은 Dr.NICE / 의사랑 / 메디칼라이즈 등 별도."

---

## 9. Observability

### 9.1 Already shipped

- `scheduler.fetch.done` / `scheduler.distill.done` structured logs (fly logs)
- `scheduler.cost.daily` rollup
- `scheduler.health` 24h silent channel canary

### 9.2 Phase A additions (Q3 2026)

- **Discord webhook alerts** when:
  - cost over budget
  - distill failed 3+ times in a row
  - any channel silent > 48h
  - PII auto-mask triggered (admin sees count, not content)
- **Per-tenant metrics dashboard** (`/sediment/admin/metrics`):
  - Events ingested (24h / 7d / 30d)
  - Decisions extracted
  - Members active
  - Cost USD

### 9.3 Phase B additions (Q4 2026)

- **Per-strategy precision tracking**: tenant admin can mark decisions "good"/"bad" → feeds dashboard
- **Connector health page**: each connector shows last successful fetch, error count, watermark age
- **Audit log viewer**: admin browses last 30 days of who-saw-what

### 9.4 Phase C (Q1 2027)

- Prometheus scrape endpoint at `/metrics`
- Per-tenant SLA dashboard (uptime, p99 latency)
- Cost forecasting (next month projection based on trend)

---

## 10. Deployment Topology

### 10.1 Y1 (now → Q3 2027): Shared SaaS only

- Single Fly VM (NRT) per Sediment instance — current `hypeproof-sediment` app
- Single Supabase Pro DB (multi-tenant via RLS)
- Single R2 bucket (multi-tenant via key prefix)
- APScheduler in-process (handles ≤ 50 tenants comfortably)
- Auto-scale trigger: > 5 sustained jobs/sec OR > 70% CPU OR > 80% RAM → manual investigation

**Cost at 50 tenants**: ~$200/mo total infra (Fly + Supabase + R2 + LLM amortized).

### 10.2 Y2 (Q4 2027+): Add Enterprise dedicated option

For Enterprise tier ($999+/mo) — optional dedicated VM:
- Per-tenant Fly app (e.g., `sediment-<tenant_slug>`)
- Per-tenant Supabase project (data isolation)
- Tenant-scoped R2 bucket
- Worker pool isolated (Celery cluster per tenant)
- Tier change Stripe webhook triggers Terraform provision

### 10.3 Y3+ (2027): On-prem option (only if requested)

- Helm chart for Kubernetes
- Air-gapped support (BYO LLM provider — Anthropic via private VPC, or local Llama)
- Customer-managed encryption keys (BYOK)

---

## 11. Phased Build Plan

### Phase A (Q3 2026, 6-8월) — PIPA-clean MVP for D + A beachhead

**6-week implementation queue:**

| Week | Build | Output |
|---|---|---|
| W1 | Voice memo connector MVP | `/v1/ingest/audio` + Whisper + voice_dump strategy |
| W2 | Photo OCR connector | `/v1/ingest/photo` + Claude vision + paper_minutes strategy |
| W3 | Meeting recording + consent | `consent_records` table + frontend consent UI + meeting upload |
| W4 | Quota enforcement (Free + Studio) | events/voice min/photo count caps + usage meter UI |
| W5 | audit_log + DSR endpoints | 4 PIPA-required migrations + admin viewer |
| W6 | PIPA-compliant onboarding flow | white-glove playbook automation + boah pilot |

**Concurrent (non-blocking):**
- DPA template + 처리방침 1차 draft (lawyer review pending)
- KakaoWork connector spike (admin OAuth research)
- Discord webhook alerts wired

**Phase A exit criteria** (Q3 end):
- 3-5 D archetype paying tenants (consulting alumni)
- 1-2 A archetype pilots (보아 + 1)
- Per-tenant cost ≤ $8/mo measured
- PIPA self-audit pass (internal checklist)
- Boah-dental case study published (internal-only memo)

### Phase B (Q4 2026, 9-11월) — Self-serve onramp + B entry

| Week | Build |
|---|---|
| W1-2 | Slack connector |
| W3 | Notion connector |
| W4-5 | Stripe billing integration + 14-day trial flow |
| W6 | Free tier onboarding flow (full self-serve) |
| W7 | KakaoWork connector |
| W8 | Governance auto-execute (PII redact, anomaly alert) |
| W9-10 | Resource-level ACL (visibility + sensitivity) |
| W11 | Per-tenant metrics dashboard (admin UI) |
| W12 | "Why us vs Glean" comparison landing page |

**Phase B exit criteria** (Q4 end):
- 15-20 paying tenants
- 30-day average retention > 80%
- Average tenant cost-to-serve ≤ $10/mo
- Self-serve signup conversion ≥ 30%

### Phase C (Q1 2027, 12-2월) — C archetype + scale

- 카톡 BYOData export upload flow
- "회사 매뉴얼" auto-generated playbook (C hero feature)
- Multi-language UI (EN toggle)
- Per-tenant prompt override (`tenant_prompt_overrides` table)
- 30-day extended trial for C archetype
- 안양 가상오피스 partnership integration

**Phase C exit criteria** (Q1 end):
- 30-50 paying tenants
- ₩5-10M MRR
- 3+ C archetype customers (proof of concept)
- < $4M ARR projection (Y1 close)

### Phase D (Q2-Q3 2027, 3-8월) — Scale GTM

- Conference presence (Korea SaaS / startup events)
- B2G channels (중소기업진흥공단, 소상공인진흥공단)
- Annual prepay automation
- Tier auto-downgrade on payment fail
- Move scheduler to Celery+Redis (50+ tenants)
- audit_log retention policies + archival to cheaper storage

**Phase D exit criteria** (Q3 2027 end):
- 80-150 paying tenants
- ₩15-30M MRR
- Cost per tenant ≤ $15/mo
- First Enterprise tier customer (₩1M+/mo)
- HypeProof Lab → Sediment company spin-out evaluation

### Phase E (Q4 2027+) — Compliance + Enterprise

- SOC 2 Type 1 audit (if US sales > 20% of revenue)
- GDPR readiness (if EU sales materialize)
- Enterprise dedicated instance offering (per §10.2)
- On-prem helm chart (if 2+ requests)

---

## 12. Engine Invariants (do not break)

These principles must hold across all phases:

1. **PIPA-clean by design** — KakaoTalk 일반 단톡방 auto-fetch never enabled, ever.
2. **Citation guarantee** — every RAG answer cites at least one artifact. No uncited assertions.
3. **"Do not invent" guard unremovable** — even tenant addendums can't relax distill safety.
4. **Confidence threshold floor** — tenants can lower but never below 0.5 (distill) / 0.55 (governance).
5. **Tool schema frozen** — downstream parsers depend on shape; strategies can't redefine.
6. **Cost tracking can never break extraction** — record_call() swallows errors.
7. **Consent before processing** — meeting recordings refuse to distill without consent_record_id.
8. **Audit log on every read** — Phase A onwards, every artifact read writes to audit_log.
9. **Tenant data isolation** — RLS enforced at DB level, JWT scope at API, prefix at storage.
10. **No SAML / SSO / GDPR / HIPAA / SOC 2 until ICP demands it** — Y1 is Korean SMB only.

---

## 13. Open Decisions (Jay 결정 필요)

Consolidating from ICP §8 + voice-ocr §10 + new engine-level:

### A. Phase A scope
1. ✅ Voice + photo OCR P1 confirmed (ICP rev 2)
2. Phase A 6-week scope locked? Or compress to 4 weeks if too aggressive?
3. Transcribe provider default: Whisper vs Gemini 2.5 audio vs self-host?
4. OCR provider default: Claude vision vs Google Cloud Vision?

### B. Pricing & quotas
5. Free tier quotas (100 events / 30 voice min / 50 photos / 7-day history) — too tight or right?
6. Starter ₩49K tier add? (between Solo $19 and Studio $99 for A/C archetype)
7. Studio rename to "Team"? Or keep "Studio"?

### C. Compliance
8. DPA template — lawyer review needed (Jay 변호사 선정?)
9. 처리방침 draft — who writes 1차?
10. Annual security self-audit cadence — Q1 / Q3?

### D. Beachhead
11. Q3 target: 5 / 7 / 10 tenants?
12. 보아치과 6-week flow execution — start week of 5/27?
13. D archetype outreach — Jay personal direct? Or systematic email campaign?

### E. Engineering
14. Scheduler migration trigger to Celery — defined criteria?
15. R2 vs Supabase Storage for object storage — confirm R2?
16. Audit_log retention — 1 year hot + 3 years cold?

---

## 14. Linked artifacts

### In-repo docs
- [ICP-segmentation.md v1.1](./ICP-segmentation.md) — customer definition (drives this doc)
- [voice-ocr-connector-spec.md](./voice-ocr-connector-spec.md) — Phase A connector details
- [collection-and-distillation.md v0.3](./collection-and-distillation.md) — historical reference (superseded)
- [boah-dental-flow.md](../demo/boah-dental-flow.md) — A archetype first customer playbook
- [supabase-pro-upgrade.md](../runbooks/supabase-pro-upgrade.md) — infrastructure runbook

### Memory pointers
- pricing-strategy-sediment
- icp-segmentation-sediment
- sediment-mvp-live
- dogfood-activation-loop
- hyrox-product-lineage

### Code anchors (already shipped)
- `lab_lib/connectors/{base,discord}.py` — connector framework
- `lab_lib/prompts.py` — strategy loader
- `lab_lib/cost_tracker.py` — llm_calls + daily rollup
- `services/sediment/prompts/{distill,governance}/` — strategy YAMLs
- `services/sediment/scripts/{scheduler,distill,consolidate_memory,discord_fetch}.py`
- `services/sediment/config/cron.yaml`
- `infra/deploy/{Dockerfile,supervisord.conf,fly.toml,start.sh}`

---

## 15. Migration from v0.3

v0.3 (`collection-and-distillation.md`) is **superseded but not deleted** — kept as historical reference. Major deltas:

| v0.3 | v1.0 (this doc) |
|---|---|
| Generic "tenant" focus | 4 specific archetypes (A/B/C/D) drive design |
| KakaoTalk auto-fetch envisioned | KakaoTalk auto-fetch **NEVER** (PIPA) |
| Voice/OCR not specified | Voice + photo OCR = P1 |
| Governance all propose-only | 2-tier: auto-execute (safety) + propose (value) |
| RBAC vague (per-resource ACL planned) | RBAC simplified to 3 roles + Phase B resource ACL |
| Compliance roadmap unclear | Korean PIPA only Y1; SOC2/GDPR Q4 2027 reconsider |
| No phasing | 5 phases (A-E) with exit criteria |
| Scheduler unspecified | APScheduler Y1 → Celery Y2 with migration trigger |

---

*This is the locked architecture spec for Sediment SaaS v1.0. All Phase A-E build work proceeds from this doc. Updates require explicit version bump (v1.1, v1.2 …) and changelog entry.*

**Version:** v1.0 (2026-05-21)
**Authors:** Jay Lee + Claude collaboration
**Next review:** Phase A end (Q3 2026 close) — incorporate live tenant learnings
