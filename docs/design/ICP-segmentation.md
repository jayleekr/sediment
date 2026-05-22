# Sediment ICP Segmentation v1.1

**Status:** Draft 2026-05-21 rev 2. Locks the customer definition that drives engine architecture, connector priorities, pricing, and GTM.
**Supersedes:** The implicit "Glean-killer for mid-market" assumption baked into the Studio $99 lead tier (which is correct directionally but mis-targeted at audience).
**v1.1 changes (2026-05-21 PM):** PIPA legal pivot. KakaoTalk general 단톡방 connector → ❌ NEVER (PIPA + 의료법 violation risk). Voice/recording/photo OCR connectors → P1. A and C archetypes data sources rewritten. New §9 Legal & Compliance reality.

> **Why now:** Before designing the Collection Engine v1 architecture, the ICP must be defined or we'll build for an imagined enterprise buyer we'll never reach. Jay's correction (2026-05-21): our real ICP is **data-pipeline-zero SMBs**, not Glean's customers. Second Jay correction (same day, PM): KakaoTalk general 단톡방을 자동 ingest하는 건 PIPA 위반 명백 → 우리의 가치 prop을 "음성/회의녹음/문서 OCR" 기반으로 재구성.

---

## 0. TL;DR

- **4 archetypes** (all valid, not mutually exclusive):
  - **A. Dental / small clinics** (5-30 person practice) — *warm lead via 보아치과 5/26*
  - **B. Small Korean SMBs** (10-100 employees, <₩5B annual revenue)
  - **C. Zero-data-pipeline companies** (everything lives in 카톡 + heads)
  - **D. Post-consulting alumni** (HypeProof Lab natural funnel)
- **Beachhead: D + A in parallel** (Q3 2026), expand to B/C self-serve from Q1 2027
- **Biggest architectural implication (rev 2): Voice + meeting recording + paper-photo OCR are P1**. ❌ KakaoTalk general 단톡방 ingest is OFF the table (PIPA + 의료법). Slack/Discord/KakaoWork P2 (workspace-admin consent umbrella).
- **Pricing reconsidered**: ₩49K Starter tier needed alongside current 5-tier funnel
- **Engine design proceeds AFTER** this lock — engine = "the platform that serves these 4 archetypes well"

---

## 1. Why "Glean-killer" framing was wrong

The Studio $99 vs Glean $500 comparison is true math but wrong target. Reality check:

| What we assumed | What ICP actually looks like |
|---|---|
| Mid-market tech company tired of Glean's price | SMB / clinic that never had Glean (or knew it existed) |
| Has Slack + Notion + Google Workspace already | Has KakaoTalk + maybe Notion (어수선) + 종이 |
| Wants to upgrade existing data pipeline | Has no data pipeline to upgrade |
| Has IT person managing tools | Has owner-operator doing everything |
| Buys SaaS for $50-100/seat normally | Anchored at ₩50K-100K/mo total for "그런 거" |
| Cares about SAML SSO + audit + compliance | Cares about "직원이 결정 사항 못 찾는 거" |

→ We're not selling to Glean's customers. We're selling to people who've never used Glean and never will. **Different category entirely** — closer to "first-time CRM for clinics" than "Notion competitor".

---

## 2. The 4 Archetypes

### 🦷 Archetype A — Dental / Small Clinics

**Profile**
- 5-30 person practice (원장 1-3 + 위생사 + 코디 + 데스크)
- Revenue: ₩500M-₩5B/yr typical
- Korean dental market: ~24,000 active clinics
- Adjacent: 한의원, 피부과, 안과, 정신과 (similar shape, ~50K total clinics in KR)

**Data landscape (rev 2 — PIPA-clean only)**
- **❌ EXCLUDED from Sediment vault**: 환자 카톡 1:1, 진료차트, 환자 응대 SMS (PIPA + 의료법 26조 — 절대 안 건드림. 이게 영업 포인트.)
- **✅ Voice memos (원장)**: 음성 dump → 운영 노하우 자산화 (원장 본인 동의)
- **✅ Meeting recordings**: 월례 회의 / 직원 교육 (참가자 사전 동의 워크플로우)
- **✅ Paper meeting minutes (사진)**: 종이 회의록 OCR
- **✅ KakaoWork (선택)**: 도입한 곳만, admin OAuth 흐름 (~10% 치과)
- **✅ Slack/Discord (선택)**: 흔치 않지만 일부 modern 치과 도입
- **외부 시스템 미터치**: 진료차트 시스템 (Dr. NICE / 의사랑 / 메디칼라이즈)는 별도 솔루션 — Sediment 안 건드림

**Decision rhythm**
- 주 2-3회 운영 결정 (시술 가격 조정, 신규 장비 도입, 환자 응대 정책, 직원 업무 분담)
- 월 1회 정기 회의 (전체 직원)
- Daily standups: 카톡 단톡방 + 잠시 모이는 morning huddle

**Pain points (verified via 보아치과 prep)**
1. **신입 위생사 인수인계 매번 0부터** — "원장님 이건 어떻게 하셨었어요?" 매일
2. **작년 결정한 시술 가격 변경 못 찾음** — 카톡 검색 한계
3. **환자 컴플레인 대응 SOP 비명문화** — 사람마다 다르게 처리
4. **원장 부재시 의사결정 마비** — 다 원장 머릿속
5. **세무 / 노무 관련 결정 추적 안 됨** — 외부 회계사/노무사와 카톡으로만 소통

**Sediment value prop (rev 2 — PIPA-safe)**
> "환자 데이터는 절대 안 만집니다. 원장님 머릿속 운영 노하우 + 직원 회의 결정 + SOP만 검색 가능한 회사 자산으로."

핵심: **운영 SOP 자동 정리 + 인수인계 자료 자동 생성**. 환자 응대 사례 archive는 ❌ (의료법/PIPA 위험). 대신 "응대 사례 SOP" 형태로 익명화·일반화된 패턴 추출.

**Pricing tolerance**
- 진료차트 시스템 ($300-500/mo) 보다 저렴해야 함 (그게 더 중요해서 1순위)
- **Sweet spot: ₩50,000-100,000/mo** ($37-75)
- 연간 결제 (₩540,000/yr) 선호 (세금 처리 + 한 번에 결재)
- ₩99K Studio도 가능하지만 입문은 더 가벼워야

**Acquisition channel**
- ✅ **보아치과 5/26 맛빼기 세션** — 이미 진행 중인 warm path
- 치과 원장 네트워크 (Jay 컨설팅 alumni)
- 치과 협회 / 학회 발표
- 의료기기 vendor 채널 (장비 살 때 같이 끼워팔기)
- ❌ 검색 광고는 비효율적 (CAC > LTV)

**Sales motion**
- White-glove. 영업이 직접 셋업.
- 1주차: 카톡 단톡방 1-2개 연결 + 회의록 1건 import
- 2주차: AI가 추출한 결정/액션 함께 검토 → "이게 다 자동이에요"
- 3주차: 신입 위생사 시뮬레이션 → "검색 한 번에 답"
- 4주차: 계약 (월 ₩90K)

**Required product features (rev 2)**
- 🔴 **Voice memo input** (원장 음성 dump → text + 결정 추출)
- 🔴 **Meeting recording + 참가자 동의 워크플로우** (회의 시작 전 명시적 동의)
- 🔴 **종이 사진 OCR** (회의록 / 메모 / 화이트보드)
- 🔴 **PII auto-mask** (혹시 흘러들어온 환자명/번호 자동 redact, `redact_pii.yaml` propose→execute 격상)
- 🟡 SOP 자동 정리 strategy (`sop_capture` 신규 prompt)
- 🟡 KakaoWork connector (admin OAuth, P2)
- 🟢 의료법 26조 / PIPA 부합 명시 + 영업 도구화 (signed DPA template)
- ❌ **환자 카톡 자동 ingest** — 금지. 영업 멘트 "환자 데이터는 안 만집니다"

---

### 🏢 Archetype B — Small Korean SMB (매출 50억 미만)

**Profile**
- 10-100명 직원
- Revenue: ₩1B-₩5B/yr
- Korean 중소기업 데이터: 통계청 기준 ~700,000개 사업체 (10명 이상 ~70,000개)
- Sectors: 제조 (소규모 가구/식품) / 유통 / 서비스 / 미용 / 학원 / 카페체인 / 인테리어

**Data landscape**
- **Slack 또는 카톡 단톡방** (50/50 분포, 업종별로 갈림)
- Notion (어수선한 페이지 5-50개) — 약 30%
- Google Workspace 또는 네이버웍스 — 60%
- 종이 회의록 + 사장 머릿속 — 40% (특히 제조/유통)
- 카카오워크 / 잔디 / 협업툴 — 약 15%

**Decision rhythm**
- 주 5-10회 결정 (가격 / 인사 / 신규 사업 / 거래처 정책)
- 일 1-2회 운영 결정 (사장 → 팀장)
- 분기 1회 전략 회의

**Pain points**
1. **회의록 분실** — Notion에 적었지만 어디 적었는지 모름
2. **책임 소재 불명확** — "누가 그렇게 결정했지?" 흔한 분쟁
3. **신입 onboarding 매번 처음부터** — 사수마다 다르게 가르침
4. **사장 의존도 100%** — "사장님이 알아"가 default
5. **거래처별 컨택 히스토리 분실** — 영업/CS 사람 바뀌면 0

**Sediment value prop**
> "회의에서 결정된 거, 카톡에서 합의된 거, 누가 약속한 거 — 다 검색 가능한 회사 메모리로"

핵심: **회의록 자동화 + 결정 사항 추적 + 책임 소재 명확화 + 신입 인수인계**

**Pricing tolerance**
- Notion ($10/seat × 10명 = $100) 비싸다고 안 쓰는 회사 많음
- 카카오워크 ($5/seat) 정도 익숙
- **Sweet spot: ₩99,000-199,000/mo** ($75-150) for 10-30명 회사
- Studio $99 (₩130K) 적정

**Acquisition channel**
- 컨설팅/강의 (Jay + HypeProof Lab 자연 funnel)
- LinkedIn / 잡플래닛 / 사람인 광고 (CTO/대표 타깃)
- 중소기업진흥공단 / KISA 디지털 전환 지원사업 연계
- 사장님 커뮤니티 (안양 가상오피스 같은 곳)
- 컨퍼런스 (스타트업 / SaaS 행사)

**Sales motion**
- Mix: 첫 5-10 tenant white-glove, 그 후 self-serve + 상담 옵션
- 14-day trial → 자동 결제 전환 (Stripe)
- 첫 미팅 (60분): demo + 그들의 카톡/Slack에 직접 연결 → 5분 안에 결과 보여줌
- 후속: 영상 onboarding 콘텐츠 + 채팅 지원

**Required product features**
- 🔴 **Slack connector** (글로벌 SMB) + **KakaoTalk** (한국 특화)
- 🔴 **Notion connector** (어수선한 페이지에서도 결정 추출)
- 🔴 Free tier (체험 후 전환 funnel — 이미 추가됨)
- 🟡 **Email connector** (영업/CS 히스토리)
- 🟡 Google Drive / Office365 connector
- 🟢 Usage meter UI (Free 한도 가시화)

---

### 📂 Archetype C — Zero Data-Pipeline Company

**Profile**
- 사실상 archetype A / B의 부분집합이지만 특히 더 "맨바닥"인 경우
- 5-50명, 매출 1B-3B
- 거의 모든 게 사장 머릿속 + 카톡
- **Notion / Slack 아예 없음**

**Data landscape (rev 2 — PIPA-clean only)**
- **❌ EXCLUDED**: 직원/거래처 카톡 단톡방 자동 ingest (PIPA 위반 위험)
- **✅ 사장 본인 음성 메모** (BYOData, 본인 동의 깨끗)
- **✅ 회의 녹음 + 동의** (직원 회의 = 근로계약상 회사 자료, 단 사전 고지 필요)
- **✅ 종이 / 화이트보드 / 사장 노트 OCR**
- **✅ 카톡 BYOData 옵션**: 사용자 본인이 "대화 내보내기" → 우리 vault에 수동 업로드 (자동화 X, 정보주체 본인 행위라 법적 깨끗)
- **외부 협업툴 권유 (장기)**: 30-90일 차에 Discord/Slack 또는 KakaoWork 도입 컨설팅 포함

**Decision rhythm**
- 사장 단독 결정 80%
- 팀장 위임 결정 20%
- 회의 = "잠깐 모여서 카톡으로 결론 적기"

**Pain points**
1. **사장 머릿속이 single point of failure** — scaling 불가
2. **채용해도 onboarding 불가** — 가르칠 매뉴얼 없음
3. **사장이 못 받아주면 모든 결정 멈춤** — 휴가/병가시 마비
4. **고객 정보 분실** — 사장 카톡으로만 소통한 거래처
5. **세무/노무 분쟁시 증거 부족** — "구두로 합의했는데..." 흔함

**Sediment value prop**
> "사장님 머리속의 회사 운영 매뉴얼을 자동으로 생성합니다"

핵심: **사장 머릿속 → 시스템화. 첫 데이터 인프라.**

**Pricing tolerance**
- 가장 낮음. ₩30-50K/mo (≈ $25-40)
- 또는 1년 prepaid ₩300-500K
- "이거 도입하면 진짜 회사 돌아가요?" 의심 강함 → 무료 체험 길게 필요 (30일+)

**Acquisition channel**
- 컨설팅에서 만남 (Jay direct)
- 1인 사장님 네트워크 (자영업자 / 소상공인 협회)
- 안양 가상오피스 같은 공유오피스 (Jay 사업계획서 — `anyang-business-plan.md`, private memory note)
- 가장 어려운 segment — 가장 절실하지만 가장 저렴

**Sales motion**
- 100% white-glove. 영업이 카톡 단톡방까지 들어가서 셋업.
- 첫 1개월: 사용법 1:1 코칭
- 그 후: 월 1회 체크인
- LTV가 낮지만 referral 효과 강함 (사장 네트워크)

**Required product features (rev 2)**
- 🔴 **Voice input** (사장 머리속 → 음성 dump → 시스템화) — 가장 큰 lever
- 🔴 **종이/메모장 사진 OCR**
- 🔴 **30일 무료 체험** (Free tier보다 적극적, white-glove 동반)
- 🔴 **카톡 BYOData export 업로드** (사용자 본인 export → 우리 upload)
- 🟡 **"회사 매뉴얼" 자동 생성 모드** (단순 검색 X, 적극 큐레이션)
- 🟡 **데이터 파이프 셋업 컨설팅 패키지** (Discord/Slack/KakaoWork 도입 with white-glove)
- 🟡 한국어 UX (전부 한글, 영어 toggle 없어도 됨)
- ❌ **카톡 단톡방 자동 fetch** — 금지

---

### 🎓 Archetype D — Post-Consulting Alumni

**Profile**
- Jay / HypeProof Lab의 강의/컨설팅 받은 사장님들
- 보통 archetype A 또는 B와 겹침
- 차이: 우리와 이미 신뢰 관계 + AI 가치 인지 + 실제 변화 의지

**Memory 근거**
- `MEMORY.md` / SK바이오팜 Academy (2026-05-14, v0.3 approved, 8명 멤버)
- `boah-dental-academy.md` / 보아치과 Academy (5/26 진행)
- `hyrox-product-lineage.md` / HYROX Lineage — 4개 케이스 누적
- HypeProof Lab 자체가 ref customer

**Data landscape**
- 컨설팅 받은 회사 = 이미 일부 Notion/Slack 사용 중일 확률 높음
- Discord (HypeProof Lab과 동일 도구 채택 가능)

**Decision rhythm**
- 컨설팅 후 자력 운영 단계
- AI 도구 사용 의지 있음 + 사용 경험 있음
- 의사결정 빈도: B와 유사 (주 5-10회)

**Pain points**
1. **컨설팅 끝나면 자력으로 못 굴림** — Jay 손 떼면 다시 옛날로
2. **AI 도구 학습 곡선** — Cline / Cursor 등 익혔지만 운영 시스템화는 어려움
3. **HypeProof Lab 멤버 인사이트 access 끊김** — 컨설팅 종료 후 단절

**Sediment value prop**
> "컨설팅 1회분이 매월 운영 시스템으로 이어집니다. HypeProof Lab 멤버 노하우는 RAG로 접근 가능."

핵심: **Continuous deal (one-shot consulting → recurring SaaS) + community access**

**Pricing tolerance**
- 가장 높음. ₩200-500K/mo 정당화 가능 (이미 컨설팅에 큰돈 씀)
- Pro tier ($299) 자연스러움
- Bundle: "강의 1회 + 1년 Sediment Pro" 패키지

**Acquisition channel**
- ✅ **현재 HypeProof Lab consulting/lecture pipeline**
- Zero CAC — 이미 우리 funnel에 들어와 있음
- 컨설팅 청구서에 Sediment 1년 자동 포함 option

**Sales motion**
- 컨설팅 마지막 세션에 "이거 우리가 매일 쓰는 도구입니다" 소개
- 30일 무료 + 자동 Pro 전환
- 분기 1회 HypeProof Lab 멤버와의 Q&A session 포함

**Required product features**
- 🔴 RAG chat 강력 (멤버 인사이트 접근)
- 🔴 컨설팅 자료 import (Notion/Drive 일괄 ingest)
- 🟡 "Jay's curated knowledge" 형태의 큐레이션 (premium)
- 🟢 Discord connector (HypeProof Lab과 동일 환경)

---

## 3. Beachhead Recommendation

**Q3 2026 (6-8월) — Beachhead: D + A 병행**

### Why D first
- **Zero CAC** — 이미 funnel 안에 있음 (강의/컨설팅 alumni)
- **즉시 매출** — 첫 3-5 tenant 8월 안에 close 가능
- **Reference customer** — HypeProof Lab 자체가 살아있는 demo
- **가격 저항 낮음** — 이미 컨설팅에 큰돈 쓴 사람들
- **빠른 product-market-fit 검증** — 30일 내에 retention 수치 확보

### Why A in parallel
- **보아치과 5/26 맛빼기 진행 중** — 다음 단계 이미 있음
- **TAM 큼** — 5만 개 의료기관 (치과 + 한의원 + 안과 등)
- **KakaoTalk connector 정당화** — 이거 만들면 A + B + C 다 열림
- **명확한 vertical** — "치과 운영 노하우 SaaS"로 포지셔닝 가능

### Q3 목표 (6-8월)
- **D archetype: 3-5 paying tenant** (월 ₩2-5M MRR)
- **A archetype: 1-2 pilot (보아치과 + 1개)** — 가격 ₩90K, 데이터 수집 우선
- KakaoTalk connector Phase 1 (MVP)

### Q4 2026 (9-11월) — Expand to B
- A에서 retention/case study 확보
- B archetype 대상 marketing 시작 (LinkedIn + 사장님 커뮤니티)
- Self-serve onboarding 강화
- 목표: 10-15 paying tenant 누적

### Q1 2027 (12-2월) — C + Self-serve scale
- 안양 가상오피스 partnership 활성화 → C archetype 진입
- 30일 무료 체험 자동화
- 목표: 30-50 paying tenant 누적

---

## 4. Cross-Archetype Product Implications

### 4.1 Connector priority (rev 2 — PIPA pivot)

| Connector | A 치과 | B SMB | C 무파이프 | D Alumni | Priority |
|---|---|---|---|---|---|
| **🎤 Voice input** (음성 메모 + 회의 녹음 with 동의) | 🔴 essential | 🟡 important | 🔴 essential | 🟡 some | **P1** |
| **📷 사진 OCR** (종이 회의록 / 메모 / 화이트보드) | 🔴 essential | 🟡 important | 🔴 essential | 🟢 nice | **P1** |
| **Discord** | 🟢 nice | 🟡 some | 🟢 rare | 🔴 essential | **P2** |
| **Slack** | 🟢 rare | 🔴 essential | 🟢 rare | 🟡 some | **P2** |
| **KakaoWork** (admin OAuth, NOT 일반 카톡) | 🟡 일부 | 🟡 일부 | 🟢 rare | 🟢 nice | **P2** |
| **Notion** | 🟡 some | 🔴 essential | 🟢 rare | 🔴 essential | **P2** |
| **Google Drive** | 🟡 some | 🟡 some | 🟢 rare | 🟡 some | **P3** |
| **Email** | 🟡 some | 🟡 some | 🟡 some | 🟢 nice | **P3** |
| **카톡 BYOData export upload** (수동, 사용자 본인) | 🟡 일부 | 🟡 일부 | 🟡 일부 | 🟢 rare | **P3** |
| **GitHub** | 🟢 no | 🟢 no | 🟢 no | 🟡 some | P4 |
| **❌ KakaoTalk 일반 단톡방 자동 fetch** | ❌ NEVER | ❌ NEVER | ❌ NEVER | ❌ NEVER | **NEVER** |

→ **Voice + 사진 OCR이 P1**. KakaoTalk 일반 단톡방 자동 ingest는 PIPA 위반 (§9 참조). 우리 차별점은 "법적으로 안전한 데이터 흐름 + 음성/문서 native".

### 4.2 Distill strategy 신규 필요 (rev 2)

기존 4개 (chat_thread / meeting_transcript / doc_edit / code_change) 외 추가:

| 신규 strategy | 용도 | 우선 archetype |
|---|---|---|
| `sop_capture` | 반복되는 업무 절차 → SOP 문서 자동 추출 | **A, C** |
| `voice_dump` | 사장님 음성 메모 → 구조화된 결정/지시사항 | **A, C** |
| `paper_minutes` | 종이 회의록 사진 → 디지털 결정/액션 | **A** |
| `vendor_thread` | 거래처 협상/약속 히스토리 (Slack/이메일 기반, PII 마스킹 必) | B |
| ~~`customer_record`~~ | ~~환자/거래처 응대 히스토리~~ | **❌ DROP** (의료법/PIPA 위험) |

→ `customer_record` 폐기. 대신 `sop_capture`가 "응대 사례 → 일반화된 SOP" 형태로 익명화 처리. 의도적으로 개별 고객/환자 단위 archive 안 만듦.

### 4.3 Pricing tier 재조정

기존 5-tier 유지하되 **이름 재검토**:

| 기존 | 가격 | Archetype fit | 한국 시장 이름 제안 |
|---|---|---|---|
| Free $0 | $0 | 모두 (acquisition) | 그대로 |
| Solo $19 | ₩25K | 1인 사업자 | Solo |
| **Studio $99** | ₩130K | A 치과 / B SMB / D Alumni | **Team** (Studio는 한국 정서에 낯섬) |
| Pro $299 | ₩400K | D Alumni / 큰 B | Pro |
| Enterprise $999+ | custom | 미래 | Enterprise |

추가 고려 (만약 가격 저항 큼 발견되면):
- ⚠️ **₩49K Starter tier** (Solo $19 ~ Studio $99 사이 갭) — A 치과/C 무파이프용
- 단, complexity 증가 — 6 tier는 선택 마비

### 4.4 Onboarding 방식

| Archetype | 방식 | 시간 |
|---|---|---|
| A 치과 | White-glove (영업 직접 셋업) | 4주 |
| B SMB | Mix (첫 5-10은 white-glove, 후 self-serve + 상담 옵션) | 2주 |
| C 무파이프 | 100% white-glove + 1개월 1:1 코칭 | 8주 |
| D Alumni | Self-serve from consulting handoff | 1주 |

→ **Q3 2026은 white-glove 압도적. Self-serve는 Q1 2027 이후.**

### 4.5 Compliance scope

| 항목 | A 치과 | B SMB | C 무파이프 | D Alumni |
|---|---|---|---|---|
| **PIPA (한국 개인정보보호법)** | 🔴 필수 | 🔴 필수 | 🔴 필수 | 🔴 필수 |
| **PII 자동 마스킹** | 🔴 필수 (환자 이름) | 🟡 권장 | 🟡 권장 | 🟢 optional |
| **의료법 26조 (진료기록)** | 🟢 비대상 (운영 데이터만) | n/a | n/a | n/a |
| **SOC 2** | ❌ 불필요 | ❌ 불필요 (한국 SMB) | ❌ 불필요 | ❌ 불필요 |
| **GDPR** | ❌ 불필요 (한국만) | ❌ 불필요 | ❌ 불필요 | ❌ 불필요 |
| **HIPAA** | ❌ 비대상 | ❌ 비대상 | ❌ 비대상 | ❌ 비대상 |

→ **한국 PIPA만 챙기면 됨**. SOC 2 / GDPR 패스 — Q4 2027 이후 글로벌 진입 시 재검토.

---

## 5. 18-Month GTM Roadmap

```
2026                          2027
 6 7 8 | 9 10 11 | 12 1 2 | 3 4 5 6 7
 │     │         │        │
 │ D + A Beachhead          
 │ (3-5 D tenant + 보아치과)
 │
       │ A 확장 + B 진입
       │ (10-15 tenant)
                  │ C + Self-serve
                  │ (30-50 tenant)
                           │ Scale GTM
                           │ (100+ tenant)
```

### Q3 2026 (6-8월) — Pilot
- 첫 5-7 paying tenant (D archetype 3-5 + A archetype 2)
- KakaoTalk connector MVP
- White-glove onboarding 표준화
- Pricing 검증: Studio ₩130K vs Starter ₩49K 시장 반응

### Q4 2026 (9-11월) — A 확장
- 치과 vertical에 focus (보아 case study → 5-10 치과)
- 한방 / 안과 / 피부과 등 인접 의료 segment 진입
- 누적 paying tenant: 15-20
- Self-serve onboarding flow v1 (Free → Solo)

### Q1 2027 (12-2월) — B + C 본격
- LinkedIn + 안양 가상오피스 partnership 마케팅
- 사장님 커뮤니티 채널 활성화
- 누적 paying tenant: 30-50
- MRR target: ₩5-10M

### Q2-Q3 2027 (3-8월) — Scale
- 컨퍼런스 / 미디어 노출 (Jay 강의 → 제품)
- B2G (중기부 / 소진공 디지털전환 지원사업)
- 누적 paying tenant: 80-150
- MRR target: ₩15-30M

---

## 6. Engine Design Implications

이 ICP 락이 다음 engine 설계 (`collection-engine-v1.md`)에 박을 axioms:

1. **Multi-tenant SaaS shared infra** — dedicated instance / on-prem 패스. Y2 후반에 다시 검토.
2. **Connector framework: Voice + 사진 OCR P1**, Discord/Slack/KakaoWork/Notion P2. ❌ KakaoTalk 일반 단톡방 자동 fetch는 영원히 금지 (§7 PIPA). GitHub/Email/Drive는 P3-4. 카톡 BYOData export는 P3.
3. **White-glove onboarding 지원**: bulk import, manual seed, fixture mode 강화. self-serve는 보조.
4. **Voice + 사진 입력은 핵심 기능** — text-only assumption 깨야 함. **신규 P1 connector 트랙**.
5. **회의 녹음 → 참가자 동의 워크플로우 필수**: 회의 시작 전 명시적 동의 체크 (PIPA 부합).
6. **PII 마스킹 자동화**: redact_pii.yaml prompt를 propose → execute로 격상. (Q3 2026)
7. **한국 PIPA 부합 audit log + 위탁계약 + 처리방침**: 영업 시작 전 필수 (Q3 2026).
8. **`customer_record` strategy 폐기**: 의료법/PIPA 위험. SOP 일반화 (`sop_capture`)로 대체.
9. **가격 enforcement는 quota-driven, not seat-driven**: events/mo + connectors + history days + voice minutes/mo.
10. **3-layer RBAC 단순화**: 사장 (admin) / 팀장 (manager) / 직원 (member). SAML 패스.
11. **Scheduler: APScheduler in-process 충분 Y1**. Celery 도입은 50+ tenant 후.
12. **Compliance: 한국 PIPA만**. SOC 2 / GDPR 패스 Q4 2027까지.
13. **정보주체 권리 이행** (열람/삭제/이의제기): Q4 2026 (외부 사용자 시작 전 필수).

---

## 7. Legal & Compliance Reality — PIPA Analysis (added rev 2)

### 7.1 Why KakaoTalk 일반 단톡방 자동 fetch는 금지

**개인정보보호법 (PIPA) 핵심 위반 시나리오:**

| 시나리오 | 합법성 | 근거 |
|---|---|---|
| 치과 환자와의 1:1 카톡 → vault | ❌ 명백 위반 | 환자 별도 동의 없음 + 의료법 26조 (진료기록 보호) |
| 직원 단톡방 메시지 → vault | ⚠️ 회색지대 | 직원 근로계약/취업규칙에 "회사 통신 사측 관리" 명시 + 명시적 동의 필요. 사장 단독 결정 X |
| 거래처 단톡방 | ❌ 위반 | 거래처 동의 없음 |
| 사장 본인 발신 메시지만 | ✅ 합법 | 정보주체 본인 행위 (BYOData) |
| 사장 본인 음성 메모 | ✅ 합법 | 본인 데이터 |
| 회의 녹음 (참가자 사전 동의) | ✅ 합법 | 명시적 동의 |
| 종이 회의록 사진 (외부 PII 없음) | ✅ 합법 | 회사 자료 |
| 카톡 BYOData export (사용자 본인 업로드) | ✅ 합법 | 정보주체 본인 행위 |

### 7.2 우리 책임 구조

B2B SaaS = **개인정보처리 수탁자**:
- 고객사 (사장) = 정보처리자, 합법 동의 책임은 그쪽
- 우리 = 위탁 수탁자 — 암호화/접근통제/위탁계약/표준 처리방침 부합 책임
- ⚠️ **but**: 명백히 위법인 데이터를 받으면 공동 책임 발생. "사장이 빨아오라 시켰음"으로는 면책 불가

### 7.3 우리의 PIPA 부합 요건 (operational checklist)

| 요건 | 상태 | Phase |
|---|---|---|
| 위탁계약 (DPA) 표준 템플릿 | ❌ 미작성 | Q3 2026 (영업 시작 전) |
| 처리방침 공개 (개인정보 처리방침) | ❌ 미작성 | Q3 2026 |
| 암호화: at rest + in transit | ✅ TLS + Supabase Pro encryption | done |
| 접근통제 (RBAC) | ✅ JWT + tenant RLS | done |
| 접근로그 / audit trail | ❌ events 테이블만 있음 | Q3 2026 (`audit_log` 테이블 추가) |
| PII 자동 마스킹 (혹시 흘러들어옴) | 🟡 prompt 있음, 실행 X | Q3 2026 (propose → execute) |
| 보관기간 정책 (자동 archive) | 🟡 governance prompt 있음 | Q4 2026 |
| 침해 시 통지 절차 (72h) | ❌ 미정 | Q3 2026 |
| 정보주체 권리 이행 (열람/삭제/이의제기) | ❌ 미구현 | Q4 2026 |

### 7.4 영업 도구화 — "PIPA-clean" 자체가 차별점

치과 영업 멘트:
> "저희는 환자 데이터를 절대 만지지 않습니다. 환자 카톡, 진료차트, 환자 SMS — 전부 제외. 원장님 음성 메모 + 직원 회의 녹음 + 회의록 사진만 자산화합니다. 의료법 + 개인정보보호법 모두 부합."

SMB 영업 멘트:
> "직원/거래처 카톡을 자동으로 빨아오지 않습니다. 그게 위법이라서요. 대신 회의 녹음 / 음성 메모 / Notion / Slack에서 합법적으로 동의받은 채널만 처리합니다."

→ **경쟁사 차별점**: Otter/Fireflies는 회의만, Glean은 데이터 많이 빨아 OAuth 흐름, 우리는 **법적 안전 흐름 + 한국 정서**.

## 8. Open Decisions (Jay 결정 필요)

| # | 질문 | 옵션 |
|---|---|---|
| 1 | Q3 2026 beachhead 합계 N tenant target | 5 / 7 / 10 / 다른 숫자 |
| 2 | Starter ₩49K tier 추가? | (a) 추가 / (b) Solo $19로 충분 / (c) 가격 검증 후 결정 |
| 3 | Voice + 사진 OCR connector 구현 우선순위 (rev 2) | (a) Q3 안에 둘 다 MVP / (b) Voice 먼저, 사진 OCR Q4 / (c) 둘 다 Q4 |
| 3b | KakaoWork connector 추진? | (a) Q3 MVP (admin OAuth) / (b) Q4 / (c) 사용자 요청 들어오면 |
| 4 | 보아치과 5/26 다음 단계 | (a) 즉시 1-month pilot / (b) 강의 1회 더 후 / (c) 다른 치과 추가 확보 후 |
| 5 | "Team" tier 이름변경 (Studio 한국 정서) | (a) Studio 유지 / (b) Team으로 변경 / (c) 한글명 별도 |
| 6 | Voice/사진 input 우선순위 | (a) Q3 안에 음성 MVP / (b) Q4 / (c) 사용자 요청 들어오면 |
| 7 | Self-serve onboarding 시점 | (a) Q3부터 옵션으로 / (b) Q1 2027부터 / (c) Q2 2027 이후 |

---

## 9. Linked memos

- `pricing-strategy-sediment.md` — 가격 5-tier 디테일 (private memory note)
- `boah-dental-academy.md` — A archetype warm lead (private memory note)
- `hyrox-product-lineage.md` — D archetype funnel context (private memory note)
- `anyang-business-plan.md` — C archetype partnership channel (private memory note)
- `collection-and-distillation.md v0.3` — historical engine design, now superseded by `04-collection-engine.md` and `05-distillation-pipeline.md`

---

*Locked direction 2026-05-21. Engine architecture v1 design proceeds from this ICP definition.*
