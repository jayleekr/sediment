# Voice & OCR Connector Spec v0.1

**Status:** Draft 2026-05-21 PM. Drives the P1 input modes per ICP rev 2 (post-PIPA pivot).
**Linked:** [`ICP-segmentation.md`](./ICP-segmentation.md) §4.1 (Connector priority), §7 (PIPA legal reality)
**Owners:** Backend (Jay) + Frontend (JY).

> **TL;DR:** Voice memos / meeting recordings / photo-OCR are P1 inputs because text-only connectors can't reach Sediment's actual ICP (치과 / 무파이프 / 직원 회의 중심 SMB). All three modes share a normalization pipeline: **raw upload → transcribe/OCR → NormalizedEvent → existing distill loop**. Consent workflow gates meeting recordings (PIPA).

---

## 0. Three input modes

| Mode | Source | Triggers | Distill strategy | Consent model |
|---|---|---|---|---|
| **🎤 Voice memo** | Owner / 사장 single-speaker dump | User-initiated upload (mobile/web) | `voice_dump` (new) | BYOData — user's own audio, user's own data |
| **🎙 Meeting recording** | Multi-speaker meeting capture (Zoom/Gemini/iPhone Voice Memos export) | User uploads OR Gemini API webhook | `meeting_transcript` (existing) | Sediment shows pre-meeting consent screen; participants check-in before recording starts |
| **📷 Paper / whiteboard photo** | Handwritten meeting minutes, sticky notes, whiteboards | User uploads photo or batch | `paper_minutes` (new) | BYOData — user owns paper |

All three normalize to **NormalizedEvent** (the existing dataclass at `lab_lib/connectors/base.py`), feed into the existing distill loop, end up as decisions/actions in vault.

---

## 1. Architecture overview

```
┌──────────────────────┐    ┌────────────────────────┐    ┌──────────────────┐
│  User upload         │───▶│  Transcribe/OCR worker │───▶│  Normalize       │
│  (mobile/web)        │    │  (queue-backed)        │    │  (NormalizedEvent│
│                      │    │                        │    │   shape)         │
└──────────────────────┘    └────────────────────────┘    └────────┬─────────┘
                                                                    │
                                                                    ▼
                                          ┌──────────────────────────────────┐
                                          │  events table (existing)         │
                                          │  source = "voice" / "ocr"        │
                                          │  payload = {transcript, raw_url, │
                                          │             participants, …}     │
                                          └────────────────┬─────────────────┘
                                                            │
                                                            ▼
                                          ┌──────────────────────────────────┐
                                          │  Distill (existing hourly cron)  │
                                          │  strategy = voice_dump /         │
                                          │             meeting_transcript / │
                                          │             paper_minutes        │
                                          └──────────────────────────────────┘
```

**Key design point:** transcribe/OCR is **async + queued**. A 1-hour meeting recording shouldn't block the upload endpoint. User uploads → 202 Accepted → worker processes → result lands in events table.

---

## 2. Transcription / OCR providers

### 2.1 Audio transcription (Voice memo + Meeting recording)

**Top choices (May 2026 pricing):**

| Provider | Quality | KO/EN | Cost / hour audio | Notes |
|---|---|---|---|---|
| **OpenAI Whisper API** | High | ✅ excellent KO | $0.36/hr | Default, best ROI |
| **Google Gemini 2.5 audio** | High | ✅ excellent | $0.40/hr | Bundled with our Gemini stack |
| **Deepgram Nova-3** | Highest | ⚠️ EN-first | $0.30/hr | English-only ICP가 아니면 X |
| **AssemblyAI** | High | ✅ KO | $0.37/hr | speaker diarization 가능 |
| Self-hosted Whisper (faster-whisper) | Medium-high | ✅ | $0 marginal | Fly GPU 추가 $200/mo |

**Pick: OpenAI Whisper API as default + Gemini 2.5 fallback.** 1-hour meeting cost ≈ $0.36 — well within Studio's margin. Speaker diarization optional (AssemblyAI swap if needed).

### 2.2 Photo OCR

**Top choices:**

| Provider | Quality | KO handwriting | Cost / image | Notes |
|---|---|---|---|---|
| **Google Cloud Vision OCR** | High | ✅ best 한글 손글씨 | $0.0015/img | Default for Korean ICP |
| **Anthropic Claude 4.5 Haiku (vision)** | High | ✅ good 한글 | ~$0.005/img | Bundled with our LLM stack — preferred for context-aware extraction |
| **Tesseract** (self-hosted) | Low-medium | ⚠️ poor 한글 손글씨 | $0 | 노이즈 큼, 회의록 사진엔 부족 |
| **GPT-4 Vision via OpenAI** | High | ✅ good | $0.01/img | OpenAI 이미 의존 中 |

**Pick: Claude Haiku 4.5 vision** as default (already integrated, context-aware extraction beats pure OCR for messy handwriting). Google Cloud Vision as fallback for clean printed text.

---

## 3. API shape

### 3.1 Upload endpoint (single)

`POST /v1/ingest/audio` and `POST /v1/ingest/photo`

```http
POST /v1/ingest/audio
Content-Type: multipart/form-data
Authorization: Bearer <JWT>

file=@meeting-20260521.m4a
mode=meeting | voice_memo
title=2026-05-21 주간회의
participants[]=Jay  participants[]=JY  participants[]=Ryan
consent_collected=true        # required for mode=meeting
language=ko                   # optional, auto-detect default
```

```http
POST /v1/ingest/photo
Content-Type: multipart/form-data

file=@minutes-20260521.jpg     # or multiple files[]=
mode=paper_minutes | sticky | whiteboard
title=2026-05-21 임직원 회의록 (page 1)
```

**Response (both):**
```json
{
  "event_id": "evt_abc123",
  "status": "queued",
  "estimated_complete_at": "2026-05-21T03:35:00Z",
  "raw_url": "s3://sediment-uploads/<tenant>/evt_abc123.m4a"
}
```

Caller polls `GET /v1/events/evt_abc123` for `status: "ready"`. Or subscribes to a WebSocket event channel (Phase 2).

### 3.2 Pre-meeting consent flow (meeting mode only)

Before the upload endpoint accepts mode=meeting, the frontend MUST collect:

```json
{
  "meeting_id": "uuid",
  "participants": [
    {"name": "Jay", "consent": true, "consent_at": "2026-05-21T14:00:00+09:00", "method": "click-to-confirm"},
    {"name": "JY",  "consent": true, "consent_at": "...", "method": "click-to-confirm"}
  ],
  "consent_text_shown": "본 회의는 Sediment에 녹음·저장됩니다. 참가자 모두의 동의가 필요합니다.",
  "consent_text_version": "v1-2026-05"
}
```

This consent record is stored separately in `consent_records` table (new, per PIPA audit). The upload endpoint rejects mode=meeting if no consent_collected=true.

### 3.3 NormalizedEvent payload examples

**Voice memo:**
```json
{
  "source": "voice",
  "kind": "voice_memo",
  "external_id": "voice_evt_abc123",
  "ts": "2026-05-21T03:30:00Z",
  "payload": {
    "transcript": "오늘 결정한 거 정리하면 토큰 만료는 60일로 가고...",
    "audio_url": "s3://sediment-uploads/<tenant>/evt_abc123.m4a",
    "duration_sec": 180,
    "speaker": "Jay",        # single-speaker assumed for voice_memo
    "language": "ko",
    "model": "whisper-v3",
    "uploaded_at": "2026-05-21T03:30:00Z"
  },
  "member_external_id": "<jay's member id>"
}
```

**Meeting recording:**
```json
{
  "source": "voice",
  "kind": "meeting_transcript",
  "external_id": "meeting_evt_xyz789",
  "ts": "2026-05-21T05:00:00Z",
  "payload": {
    "transcript": "[Jay] 안녕하세요 ... [JY] 동의 ...",
    "transcript_diarized": [
      {"speaker": "Jay", "ts": "0:00", "text": "안녕하세요..."},
      {"speaker": "JY",  "ts": "0:12", "text": "동의 ..."}
    ],
    "participants": ["Jay", "JY", "Ryan"],
    "consent_record_id": "consent_001",   # PIPA audit pointer
    "audio_url": "s3://sediment-uploads/<tenant>/meeting_xyz789.m4a",
    "duration_sec": 3600,
    "language": "ko",
    "title": "2026-05-21 주간회의"
  }
}
```

**Paper photo:**
```json
{
  "source": "ocr",
  "kind": "paper_minutes",
  "external_id": "photo_evt_def456",
  "ts": "2026-05-21T06:00:00Z",
  "payload": {
    "ocr_text": "결정사항\n1. 시술 가격 인상 5% (6/1부터)\n2. 신입 위생사 채용 ...",
    "ocr_provider": "claude-haiku-4-5-vision",
    "image_url": "s3://sediment-uploads/<tenant>/photo_def456.jpg",
    "image_count": 3,                # multi-page
    "uploaded_by": "원장",
    "title": "2026-05-21 월례회의록 (3장)"
  }
}
```

---

## 4. Distill strategy YAMLs (new)

### 4.1 `voice_dump`

**Use case:** Single-speaker stream-of-consciousness from owner. Often unstructured; extract decisions/instructions/observations.

```yaml
# services/sediment/prompts/distill/strategies/voice_dump.yaml
name: voice_dump
version: 0.1.0
description: |
  Owner / single-speaker voice memo dumps. Often unstructured stream-of-
  consciousness with "decided to", "let's do", "we should" mixed with
  observations and ideas. Extract only explicit decisions and tasks.

extends: ../base.yaml

applies_to:
  source: voice
  kind: voice_memo

system_prompt: |
  (BASE applies. Additional context below.)

  The input is a transcript of a single person (the owner) speaking aloud.
  Voice memos differ from chat: there's no back-and-forth, no agreement
  signal. The speaker IS the decision-maker — when they say "그래 이걸로
  가자" or "결정함" or "내일까지 ○○하자", treat as MADE decision.

  Common patterns:
  - Stream-of-consciousness: "음... 그러니까... 이건 이렇게 하고...
    아 잠깐 다시" — collapse meandering, keep final position.
  - Action items spoken aloud: "내가 김대리한테 이거 시킬게" — capture
    as action with owner_hint = "김대리".
  - Ideas not yet decided: "이런 거 어떨까... 좋을 것 같은데... 음..."
    — do NOT extract as decision unless ratified ("그래 이걸로 가자").

  Anti-patterns:
  - Speculation without commitment ("이런 식으로 갈 수도 있고") → skip.
  - Observations not requiring action ("요즘 환자가 늘었네") → skip.

confidence_threshold: 0.65
min_body_chars: 60

guards:
  - "Voice memo = single decision-maker. No multi-party ratification required."
  - "Meandering / back-and-forth in the SAME memo = pick the final position."
  - "Speculation ('어떨까', '좋을 것 같다') without commitment is NOT a decision."
  - "Extract actions even without owner — owner_hint can be null."
```

### 4.2 `paper_minutes`

**Use case:** Photo of handwritten meeting minutes, sticky notes, whiteboard. OCR may have noise.

```yaml
# services/sediment/prompts/distill/strategies/paper_minutes.yaml
name: paper_minutes
version: 0.1.0
description: |
  OCR output from photographed handwritten meeting minutes, sticky notes,
  whiteboards. Handwriting noise is expected — be tolerant of OCR errors
  but conservative on extraction.

extends: ../base.yaml

applies_to:
  source: ocr
  kind: paper_minutes

system_prompt: |
  (BASE applies. Additional context below.)

  The input is OCR text from a photograph. Expect:
  - Bullet points / numbered lists (handwritten 의사록 형식)
  - Section headers like "결정사항", "할 일", "안건", "참석자"
  - Names + dates often abbreviated
  - OCR errors: "○" misread as "0" or "O", 한글 받침 누락 가능

  Korean meeting-minute conventions:
  - "결정" / "확정" / "○○로 함" / "○○하기로" = made decision
  - "TODO" / "할 일" / "○○씨" + verb = action item
  - "안건" / "논의" without resolution = NOT a decision
  - Names with 직급 ("김대리", "박과장") = owner_hint

  Be conservative: if OCR text is garbled (>30% non-Korean chars in body),
  emit empty arrays.

confidence_threshold: 0.7
min_body_chars: 80

guards:
  - "If OCR text appears heavily garbled (low Korean-char ratio), return empty arrays."
  - "Handwritten 안건 lists without resolutions are NOT decisions."
  - "Names + 직급 like '김대리' → owner_hint (strip 직급 if cleaner)."
  - "Date hints like '6월 1일부터' → due_date YYYY-MM-DD if year inferable from header."
```

### 4.3 `meeting_transcript` (existing — minor update)

The existing strategy works for multi-speaker diarized transcripts. **Update**: add explicit consent check guard.

```yaml
# (append to existing meeting_transcript.yaml guards)
guards:
  - "..."  # existing guards
  - "(rev 2) If payload.consent_record_id is missing, do NOT process. Caller bug."
```

---

## 5. Consent workflow (PIPA — required for meeting mode)

### 5.1 Database: `consent_records` table (new migration)

```sql
CREATE TABLE consent_records (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid REFERENCES tenants(id) ON DELETE CASCADE,
  consent_type    text NOT NULL,             -- "meeting_recording", "data_processing", etc.
  consent_text    text NOT NULL,             -- exact text user agreed to
  consent_text_version text NOT NULL,        -- versioned for legal audit
  participants    jsonb NOT NULL,            -- [{name, member_id?, consent, consent_at, method}]
  collected_by    uuid REFERENCES members(id),
  collected_at    timestamptz NOT NULL DEFAULT now(),
  meeting_id      uuid,                       -- optional FK to a future meetings table
  revoked_at      timestamptz                 -- 정보주체 권리 행사 (철회)
);

CREATE INDEX idx_consent_records_tenant_ts ON consent_records (tenant_id, collected_at DESC);
CREATE INDEX idx_consent_records_meeting ON consent_records (meeting_id) WHERE meeting_id IS NOT NULL;
```

### 5.2 Frontend: pre-meeting consent screen

```
┌──────────────────────────────────────────────────────────┐
│  회의를 녹음하기 전에                                       │
│                                                            │
│  본 회의는 Sediment에 녹음·저장되며, AI가 결정사항과       │
│  액션 아이템을 자동 추출합니다.                              │
│                                                            │
│  참석자 동의 (모두 체크해야 시작됩니다):                    │
│   ☑ Jay     (방금 동의함)                                  │
│   ☑ JY      (방금 동의함)                                  │
│   ☐ Ryan    (대기 중 — Ryan이 이 화면에서 직접 체크해야 함) │
│                                                            │
│  ☑ 본인은 만 14세 이상이며, 본 처리에 자유의지로 동의합니다 │
│  ☑ 정보주체 권리 (열람·삭제·이의제기) 안내를 받았습니다     │
│                                                            │
│  [▶ 녹음 시작 (전원 동의 필요)]    [취소]                  │
└──────────────────────────────────────────────────────────┘
```

Each click writes a row into `consent_records`. Recording can't start until all participants check in.

### 5.3 Post-meeting revocation flow

정보주체가 동의 철회 시:
1. `consent_records.revoked_at = now()`
2. 해당 meeting의 transcript에서 그 사람의 utterance 자동 삭제 + 임베딩 무효화
3. 이미 distill된 결정/액션 중 그 사람의 owner_hint도 자동 익명화

API endpoint: `POST /v1/consent/{consent_record_id}/revoke`

---

## 6. Worker pool (async transcription/OCR)

### 6.1 Architecture

```
upload → Postgres job queue (transcribe_jobs table) → worker poll
                                                      ↓
                                              Whisper API call
                                                      ↓
                                       INSERT INTO events (...)
                                                      ↓
                                          distill picks it up next hour
```

Use the existing APScheduler (1 worker process now) but add a **dedicated job queue** in a new table:

```sql
CREATE TABLE transcribe_jobs (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid REFERENCES tenants(id),
  event_id        uuid REFERENCES events(id),
  mode            text NOT NULL,             -- "audio" | "photo"
  provider        text NOT NULL,             -- "whisper" | "claude_vision" | …
  input_url       text NOT NULL,             -- s3://… for the raw upload
  status          text NOT NULL DEFAULT 'queued',  -- queued | running | done | failed
  attempts        int NOT NULL DEFAULT 0,
  result_payload  jsonb,
  error           text,
  enqueued_at     timestamptz NOT NULL DEFAULT now(),
  started_at      timestamptz,
  completed_at    timestamptz
);

CREATE INDEX idx_transcribe_jobs_status ON transcribe_jobs (status, enqueued_at);
```

APScheduler adds a **5-minute job**:
```python
async def _run_transcribe_worker() -> None:
    """Poll transcribe_jobs, process 1-5 queued jobs per tick."""
    # SELECT … FOR UPDATE SKIP LOCKED (5)  → process → INSERT events row
```

Why Postgres queue not Redis: we already have Postgres, this fits the "stay simple Y1" axiom. When throughput needs > 10 jobs/min, swap to Redis Streams or Celery.

### 6.2 Cost guardrails

- **Per-tenant monthly cap** on transcribe minutes (Studio: 500 min/mo, Pro: 5000, Enterprise: unlimited)
- **Per-call timeout**: 5 min max per Whisper call (longer audio → split chunks)
- **Failed attempts retry**: max 3, exponential backoff (1m / 5m / 30m)
- **Cost record**: every transcribe call writes to `llm_calls` table (extend schema to include provider="whisper", input_minutes column)

---

## 7. Storage

### 7.1 Object storage

**Provider**: Cloudflare R2 (S3-compatible, zero egress fees, much cheaper than AWS S3).
- Cost: $0.015/GB/mo storage, $0 egress
- Bucket: `sediment-uploads`
- Path: `/<tenant_id>/<event_id>.<ext>`
- Encryption: SSE-S3 (server-side)
- Retention: 90 days hot, 1 year cold (auto-tier), 3 years archive then delete
- Per-tenant signed URLs (24h expiry) for playback

Why R2 not S3: $0 egress is critical for serving audio playback to users without budget surprises.

### 7.2 Limits

| Plan | Max file size | Monthly upload quota | Storage cap |
|---|---|---|---|
| Free | 50 MB / file | 500 MB / mo | 2 GB total |
| Solo | 200 MB | 5 GB / mo | 20 GB |
| Studio | 500 MB | 50 GB / mo | 200 GB |
| Pro | 2 GB | 500 GB / mo | 2 TB |
| Enterprise | unlimited | custom | custom |

---

## 8. UI/UX

### 8.1 Upload flows

**Voice memo (mobile-first)**:
```
1. Tap [🎤 음성 메모] button (always-on FAB)
2. Record (visual waveform feedback)
3. Stop → preview transcript (auto-shown after 30s)
4. Tap [저장] → upload + queue
5. Toast: "음성 메모 저장 — 5분 안에 결정 추출 결과 알림"
```

**Meeting recording**:
```
1. Tap [🎙 회의 시작] in left nav
2. Title / participants 입력
3. Consent screen (앞 §5.2)
4. All participants check in → recording starts
5. Stop button → upload + queue
6. Tab '회의' → list view with status (transcribing / ready / failed)
```

**Photo upload**:
```
1. Drag-drop OR tap [📷 사진 추가]
2. Multi-photo accepted (up to 10 per upload)
3. Optional: 회의 제목 / 날짜 / 페이지 번호
4. Submit → queue
5. Status badge updates (pending → ready)
```

### 8.2 Result surfacing

새 결정/액션 추출되면:
- Library에 새 artifact (type=`note`, ref=`voice/<date>/<slug>` 또는 `paper/<date>/<slug>`)
- 한 사람의 음성 메모는 `voice/2026-05-21/<slug>` 같은 ref
- Discord webhook 알림 (옵션): "원장님 06:30 음성 메모 → 결정 2건 추출"

---

## 9. Phase rollout

### Phase A — MVP (4 weeks, Q3 2026 early)
- Voice memo upload (mobile + web)
- Whisper API integration
- `voice_dump` distill strategy
- Postgres job queue + APScheduler worker
- R2 object storage
- Cost tracking extension (`llm_calls` provider column)

### Phase B — Meeting recordings + consent (3 weeks)
- `consent_records` table + frontend consent screen
- Meeting upload + diarization (Whisper or AssemblyAI)
- `meeting_transcript` strategy already exists, add consent guard
- Revocation API + auto-redact downstream artifacts
- Discord webhook alerts (optional)

### Phase C — Paper OCR (2 weeks)
- Photo upload (single + batch)
- Claude Haiku 4.5 vision integration
- `paper_minutes` strategy
- Multi-page stitching (3-page meeting minutes as one artifact)

### Phase D — Hardening (ongoing)
- Per-tenant quota enforcement
- Failed-job retry + dead-letter
- Worker pool scale (Celery introduction when needed)
- Speaker diarization quality tuning

---

## 10. Open decisions

| # | Question | Options |
|---|---|---|
| 1 | Transcription provider default | (a) Whisper API / (b) Gemini 2.5 audio (we already use Gemini) / (c) Self-host Whisper for cost |
| 2 | OCR provider default | (a) Claude 4.5 vision (in-stack) / (b) Google Cloud Vision / (c) Both with cost-based routing |
| 3 | Storage provider | (a) Cloudflare R2 (cheap, $0 egress) / (b) Supabase Storage (already integrated) / (c) AWS S3 |
| 4 | Mobile UX scope Phase A | (a) Web responsive only / (b) PWA install / (c) Native React Native app |
| 5 | Consent screen wording | Need Jay (or 법무) sign-off on Korean legal text |
| 6 | Speaker diarization in Phase A or B? | (a) A (worth the cost upfront) / (b) B (only for meetings, single-speaker memos don't need it) |
| 7 | Voice memo max length default | (a) 5 min / (b) 10 min / (c) 30 min / (d) unlimited (just cost-capped) |

---

## 11. Dependencies on other work

- ✅ `lab_lib/connectors/base.py` (ConnectorABC, NormalizedEvent) — already exists
- ✅ Existing distill loop, prompt loader, cost tracker — already wired
- ❌ `consent_records` table — needs migration
- ❌ `transcribe_jobs` table — needs migration
- ❌ R2 bucket provisioned — Jay needs to create account + provide credentials
- ❌ Whisper / Claude vision API access — keys already exist (`HYPE_ANTHROPIC_KEY` + `OPENAI_API_KEY` in env)
- ❌ Frontend mobile audio recording — needs Web Audio API integration

---

*Voice + OCR connectors are the P1 unlock that makes Sediment legally serve A 치과 + C 무파이프 archetypes. Without these, our ICP can't actually use the product.*
