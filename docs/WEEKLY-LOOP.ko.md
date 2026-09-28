# How HypeProof Lab Works — 주간 운영 루프

> **목적**: AI Native하게 일하기 위한 기반. AI가 실행을 나르는 조직에서
> 속도를 결정하는 것은 실행이 아니라 사람 사이의 이해 전달이다. 이 루프는
> 그 병목을 없애고 전원이 같은 그림을 보게(sync) 만든다 — 누구에게 묻지
> 않아도 지금 어디까지 왔는지 시스템이 말해주도록.

> 이 문서는 HypeProof Lab이 매주 도는 운영 루프의 canonical 정의다.
> 회의가 이슈가 되고, 이슈가 산출물이 되고, 산출물이 다음 회의의 입력이 되는
> 흐름 전체를 다룬다. *"이번 주 루프 돌려줘"* 라고 에이전트에게 말하면 각
> 단계가 실제 명령으로 실행된다.

> **출처(provenance)**: `jayleekr/hypeproof-harness:docs/WEEKLY-LOOP.ko.md`
> 의 vendored 사본. 직접 수정하지 말 것 — 변경은 harness에서 PR로.

---

## 1. 루프 한 바퀴

```
월요일 주간회의 ─→ 자동 회의록 ─→ (다음날) AI 분해 ─→ GitHub 이슈
     ▲                                                    │
     │                                              Context/Tasks/
     │                                              Owner/ETA 포함
     │                                                    │
번다운 리뷰로 ←─ Sediment ingest ←─ 산출물 ←─ 주중 실행 ←─┘
다음 회의 시작    + members 포털              (사람 + 자율주행 루프)
```

| 시점 | 무엇 | 누가/무엇이 |
|---|---|---|
| 월요일 | 주간회의 — 지난 cycle 이슈 번다운 리뷰로 시작 | 전원 |
| 월요일 | 회의록 자동 생성 (Discord 핀 / 녹취) | 자동화 |
| 화요일 | 회의록 → 액션 아이템 분해 → repo별 이슈 발행 | AI (`/weekly-loop`) |
| 주중 | 이슈 실행 — 사람은 방향, AI는 실행 | 멤버 + 자율주행 루프 |
| 주중 | 산출물 → members 포털 게시 + Sediment ingest | 멤버 + 자동화 |
| 일요일~월요일 | pre-meeting 번다운 리포트 생성 → 회의 아젠다에 붙임 | `burndown.py` |

cycle의 이름은 **다음 회의 날짜**다. 라벨 `weekly-YYYY-MM-DD`가 그 cycle의
모든 이슈에 붙는다. 예: 7/14(월) 회의에서 나온 이슈들은 다음 회의인
`weekly-2026-07-21` 라벨을 단다 — "이 날짜 전까지 끝낼 일"이라는 뜻이다.

---

## 2. 원칙 — 이 6개가 루프의 전부다

1. **Issue-first — 기록되지 않은 일은 없는 일.** 회의에서 나온 액션이
   이슈가 안 됐으면 그 일은 존재하지 않는다. 구두 합의, Discord 스레드,
   개인 메모는 실행 단위가 아니다. 이슈만이 실행 단위다.
2. **ETA rule — 모든 이슈는 다음 회의 전 ETA를 가진다.** ETA가 다음 월요일을
   넘는 큰 작업은 그대로 두지 말고 **월요일까지의 1차 산출물**로 분할한다.
   "다음 주에 중간 결과를 보여줄 수 있는 단위"가 이슈의 최대 크기다.
3. **사람은 방향, AI는 실행.** 이슈의 Context와 판단은 사람이 쓰고, 반복
   실행·조사·초안은 자율주행 루프(에이전트)에 위임한다. `human-needed`
   라벨이 없는 이슈는 AI가 먼저 시도해도 된다 (§MEMBER-GUIDE 4.1).
   실행자가 여럿이므로 **잡은 일은 잡았다고 말한다** — `wip` 라벨로 선점하고,
   `wip`이 붙은 이슈는 사람도 크론도 집지 않는다 (§6.0, §MEMBER-GUIDE 4.1a).
4. **모든 산출물은 증거로 축적한다 — 증거 없이 닫은 이슈는 완료가 아니다.**
   실행이 끝나면 결과물이 Sediment에 ingest되고, 이슈에는 **`Evidence:` 참조**가
   남아야 한다 (§6.1). 한 일이 증거로 남지 않으면 다음 주에 같은 조사를
   반복하게 된다.

   **지금 이 원칙이 강제되는 범위는 정확히 이만큼이다**: `check.py`가 cycle 라벨이
   붙은 closed 이슈를 기계적으로 검사하고, `Evidence:` 참조도 면제도 없으면
   non-zero로 실패한다. 이 검사는 이제 두 곳에 배선돼 있다 —
   `repo-governance live audit` 워크플로의 `weekly-loop-gate` job이 매일
   03:17 UTC에 세 repo의 살아있는 이슈를 검사하고 위반이 있으면 red가 되며,
   PR CI(`test.yml`)는 고정된 적대적 코퍼스로 게이트 코드 자체를 검사해
   우회 경로가 다시 열리면 머지를 막는다. **적용 대상은 2026-07-22 00:00 KST
   이후에 닫힌 이슈다** (§6.1).
5. **문서 single source — members 포털 + harness canonical.** 프로세스
   문서는 harness가 원천이고, 멤버가 읽는 곳은 members 포털과 vendored
   사본이다. 같은 내용을 두 군데에 따로 쓰지 않는다.
6. **루프 자체도 harness로 검증한다.** "이슈에 ETA가 다 있는가"를 사람이
   눈으로 세지 않는다. `scripts/weekly-harness/check.py`가 기계적으로
   검증하고, 위반이 있으면 non-zero로 실패한다.

---

## 3. 이슈는 어느 repo로 가나

| 액션 아이템 성격 | Repo |
|---|---|
| 공개 사이트 · 콘텐츠 · 운영 · 멤버/커뮤니티 | **`hypeprooflab`** |
| Studio 제품 (VSCodium fork · 워크숍 도구 · Worker) | **`hypeproof-studio`** |
| 지식 DB · ingest · SaaS 백엔드/프론트 | **`sediment`** |
| 공유 규약 · 스킬 · 프로세스 자체의 변경 | `hypeproof-harness` (PR로) |

애매하면 **산출물이 최종적으로 사는 repo**를 고른다. 여러 repo에 걸치면
repo별로 이슈를 쪼갠다 — 하나의 이슈는 하나의 repo에서 닫혀야 한다.

---

## 4. 이슈 형식 — Context / Tasks / Owner / ETA

모든 cycle 이슈의 본문은 이 4개 섹션을 가진다. `check.py`가 이 형식을
검증한다.

```markdown
## Context

왜 이 일이 나왔나 — 회의록의 해당 대목 요약 + 링크. AI가 이어받아도
맥락을 다시 묻지 않을 만큼.

## Tasks

- [ ] 구체 작업 1
- [ ] 구체 작업 2

## Owner

담당: @github-id

## ETA

ETA: 2026-07-21
```

- **ETA는 `ETA: YYYY-MM-DD` 형식의 라인**이어야 하고, cycle 라벨의 날짜
  (다음 월요일) **이하**여야 한다.
- **Owner는 `Owner` 또는 `담당` 섹션/라인**으로 명시한다. assignee 지정도
  같이 하되, 본문에 남기는 게 기준이다 (assignee는 옮겨질 수 있다).
- 라벨: `weekly-YYYY-MM-DD` (다음 회의 날짜) 필수. 없으면 그 이슈는 이번
  cycle의 번다운에 잡히지 않는다 — 원칙 1 위반과 같다.
- **테마 라벨 `theme:<id>` 필수.** 모든 이슈는 다섯 전략 테마 중 하나에
  속한다 — 발행 시점에 붙인다. 나중에 소급하지 않는다: 라벨이 로드맵 롤업의
  유일한 연결고리라서, 빠진 이슈는 전략 화면에서 사라진다. 테마:
  `theme:education` · `theme:product` · `theme:sales` · `theme:governance` ·
  `theme:sediment` (정의는 `roadmap.strategy.json`). 어디에도 안 맞으면 그건
  이슈가 아니라 새 테마가 필요하다는 신호 — 사람에게 물어본다.

---

## 5. 화요일 — AI 분해

Claude Code에서:

```text
/weekly-loop meeting-notes/2026-07-14.md
```

스킬이 회의록에서 액션 아이템을 뽑고, repo를 배정하고, §4 형식의 이슈
초안을 만들고, 기존 open 이슈와 중복을 걸러낸 뒤, cycle 라벨과 **테마 라벨
(`theme:<id>`)** 을 만들어(없으면 생성) 이슈를 발행하고, 생성된 URL을
보고한다. 발행 전에 초안을 — repo·Owner·ETA·**테마** 배정까지 — 사람이
확인한다. 방향은 사람이 정한다 (원칙 3).

발행이 끝나면 바로 검증한다:

```bash
python3 scripts/weekly-harness/check.py --cycle weekly-2026-07-21
```

### 회의록 아카이브 (놓친 사람용)

이슈 발행과 함께 그 주 회의록을 **큐레이션본**으로 아카이브한다 — 요약 ·
다음 단계 · 상세만, 스크립트 전사와 이메일은 제외(멤버 게이트 + PII 스캔).
회의를 놓친 멤버가 `/members/meetings`에서 따라잡는다.

- 저장: consumer(site) repo의 `web/src/content/private/meeting-notes/YYYY-MM-DD.md`
  (주당 1파일). **이메일·개인 전사 금지** — PII email scan이 막는다.
- 인덱스: `node web/scripts/gen-meetings.mjs` → `meetings.index.json` 재생성.
- 페이지: `/members/meetings`(목록) · `/members/meetings/<date>`(상세). 멤버
  전용 + noindex.

회의록은 실행(이슈)과 함께 굴러 올라간다 — 각 회의록 상세는 그 주 `주간 보드`·
`로드맵`으로 링크된다.

---

## 6. 주중 — 실행과 증거

- 이슈 실행은 MEMBER-GUIDE §4의 5단계 흐름 그대로 — 선점(`wip`) → 브랜치 →
  커밋·테스트 → PR(`Closes #<N>`) → merge → auto-close.
- 이슈가 막히면 ETA 전에 이슈에 코멘트로 남긴다. 조용히 넘기는 미룸이
  가장 비싸다.
- 산출물(보고서, 문서, 배포 URL)은 members 포털에 게시하고 Sediment에
  ingest한다 (원칙 4). 이슈를 닫을 때 산출물 링크를 코멘트로 남긴다.

### 6.0 착수 게이트 — `wip` 선점

실행자가 여럿이다. 사람 여러 명, Claude Code 세션 여러 개, 크론 루프까지
같은 이슈 목록을 본다. worktree 분리는 **파일 충돌**만 막지 **중복 작업**은
못 막는다 — 두 세션이 같은 이슈를 각자 풀면 PR이 두 개 나오고 한쪽은
조용히 버려진다. 버려지는 쪽도 리뷰·CI·컨텍스트는 이미 다 쓴 뒤다.

이미 있는 것만으로 푼다. 라벨 1개 + 코멘트 1개.

| 단계 | 무엇을 |
|---|---|
| **선점(claim)** | 작업 **시작 전에** 이슈에 `wip` 라벨 + 담당자(assignee) 지정 + 세션 이름과 브랜치를 밝힌 코멘트 |
| **존중(respect)** | 모든 picker — 사람이든 크론이든 — 는 `wip`이 붙은 이슈를 건너뛴다 |
| **해제(release)** | 끝나면 `wip`을 뗀다. `Closes #<N>` PR이 머지되면 이슈가 닫히면서 자동으로 풀린다 |
| **만료(stale)** | claim 코멘트가 **24시간** 넘게 지난 `wip`은 죽은 선점이다. 코멘트로 회수 사실을 남기고 다시 잡아도 된다 |

```bash
gh issue edit <N> --repo jayleekr/<repo> --add-label wip --add-assignee @me
gh issue comment <N> --repo jayleekr/<repo> --body "claim: <세션 이름> / branch: <브랜치>"
```

코멘트가 라벨과 짝이 되어야 하는 이유는 **만료 판정 때문이다.** 라벨에는
시각이 없다. claim 코멘트의 타임스탬프가 24시간 기준의 유일한 근거다.
코멘트 없는 `wip`은 붙은 시각을 알 수 없으므로 처음부터 죽은 선점으로 본다.

> **이건 규약이지 집행이 아니다.** `wip`을 검사하는 코드는 없다. 선점 없이
> 올린 PR도, 이중 선점도 CI가 거부하지 않고 어떤 게이트도 red가 되지 않는다.
> 이 문서에서 §6.1(완료 게이트)은 `check.py`가 기계로 강제하는 **집행**이고,
> §6.0은 사람과 에이전트가 지키기로 한 **규약**이다. 둘을 섞어 읽지 말 것
> (§9.1).

### 6.1 완료 게이트 — `Evidence:` 참조

이슈를 닫기 전에, 본문이나 **닫는 코멘트**에 한 줄을 남긴다:

```
Evidence: https://github.com/jayleekr/sediment/pull/42
```

`증거:` 도 같다. URL은 **GitHub 퍼머링크 3종** 중 하나여야 한다 — 새 ID 체계를
만들지 않고 이미 있는 식별자를 그대로 쓴다:

| 형태 | 언제 |
|---|---|
| `.../pull/<n>` | 작업이 PR로 들어갔을 때 (대부분) |
| `.../commit/<sha>` | PR 없이 직접 커밋했을 때 |
| `.../issues/<n>#issuecomment-<id>` | 산출물이 코멘트 자체일 때 (조사 결과, 리포트) |

`Evidence:` 마커와 퍼머링크 형태를 **둘 다** 요구한다. 본문에 그냥 붙여둔 링크는
일상적인 이슈 대화라서, 실수로 게이트를 통과시키면 안 된다.

마커는 **주장으로 쓴 문장에서만** 인정한다. 코드 펜스, 4칸 들여쓴 코드 블록,
인라인 코드, 인용문(`>`), HTML 주석 안의 `Evidence:`는 이 문서를 붙여넣은
것이지 이 이슈가 뭘 만들었다는 주장이 아니다. `ETA:`/`Owner:`도 같은 규칙을
따른다.

**적용 시점 — 2026-07-22 00:00 KST 이후에 닫힌 이슈부터.** 그 전에 닫힌 이슈는
GitHub이 기록한 `closedAt`을 근거로 자동 면제된다. 라벨로 면제하지 않는 이유는
간단하다: 아무나 붙일 수 있는 라벨은 감사 기록이 아니다. **과거 이슈를 게이트에
맞추려고 일괄 수정하는 일은 없다.**

**면제 — 산출물이 없는 일은 막지 않는다.** 열거된 경로만 있다:

| 면제 | 어떻게 | 언제 |
|---|---|---|
| `Evidence-Exemption: cancelled` | 본문/코멘트에 한 줄 | 작업이 중단돼 산출물이 없다 |
| `Evidence-Exemption: duplicate` | 본문/코멘트에 한 줄 | 다른 이슈에서 처리됐다 |
| `Evidence-Exemption: administrative` | 본문/코멘트에 한 줄 | 설정·라벨 등 산출물 없는 운영 작업 |
| `Evidence-Exemption: no-deliverable` | 본문/코멘트에 한 줄 | 논의·결정만 남았다 |
| "closed as not planned" | `gh issue close <n> --repo <owner/name> --reason "not planned"` | 회의에서 drop한 일 — 만든 게 없다 |

코드는 **위 네 개뿐**이다. `Evidence-Exemption: 그냥 필요 없어서` 같은 자유
서술은 거부된다 — 아무렇게나 쓸 수 있는 면제는 아무도 감사할 수 없다.
`no-evidence-needed` 라벨은 더 이상 면제가 아니다 (위와 같은 이유).

면제는 게이트 출력에 `EXEMPT`로 이유와 함께 찍힌다. 조용히 빠져나갈 수는 없다.

**통과 상태는 두 가지로 구분해서 찍힌다.** 이슈를 닫은 PR을 GitHub이 직접
기록해 둔 경우(`closedByPullRequestsReferences`)는 `existence verified by
GitHub`, 사람이 적은 `Evidence:` 한 줄만 있는 경우는 `syntax_valid,
existence_unverified`다. 게이트는 URL을 열어보지 않으므로, 형식만 맞는 참조와
GitHub이 보증하는 참조를 같은 것으로 보고하지 않는다.

---

## 7. 월요일 아침 — 번다운

회의 전에 리포트를 뽑아 아젠다 맨 위에 붙인다:

```bash
# 아젠다에 붙일 마크다운
python3 scripts/weekly-harness/burndown.py --cycle weekly-2026-07-21
```

출력은 그대로 마크다운이라 아젠다 문서에 붙여넣으면 끝. notify dispatcher로
자동 발송하고 싶다면 `weekly.burndown` 이벤트 템플릿을 먼저 추가해야 한다
(`scripts/notify/templates/` — 아직 없음, 선택 과제).

리포트는 repo별 closed vs open 수와 이슈 목록(번호 · 제목 · 담당 · 상태)을
보여준다. 회의는 이 표에서 시작한다 — open으로 남은 이슈는 그 자리에서
carry-over(새 cycle 라벨로 이관 + ETA 재설정)하거나 명시적으로 drop한다.

---

## 8. 로드맵 — 주가 굴러 올라가는 곳 (Source of Truth)

주간 실행은 세 고도로 굴러 올라간다: **주(실행) → 마일스톤(이벤트) → 연간
테마(전략)**. 어느 것도 두 번 관리하지 않는다 — 데이터는 한 곳에서만 나온다.

| 층 | Source of Truth | 어디 | 갱신 |
|---|---|---|---|
| 실행 · 주간 | GitHub 이슈 + `weekly-*` 라벨 | GitHub | 화요일 분해 (§5) |
| 이벤트 | GitHub 마일스톤 + 이슈 연결 | GitHub | 분해 시 생성·연결 |
| 상태 | 이슈 활동 (PR·코멘트·이월) | GitHub | 자동 추론 |
| **전략 · 테마** | **`roadmap.strategy.json`** | consumer repo (site) | 드물게, 사람이 PR로 |
| 렌더 데이터 | `roadmap.json` · `weekly.json` | 파생 (생성됨) | 생성기가 재생성 |

원칙: **GitHub이 실행·이벤트·상태의 유일한 SoT다. 복제하지 않는다.** 새로
정본을 두는 것은 전략(테마·멤버 배치)뿐이고, 그건 사람이 PR 리뷰로만 바꾼다.

테마↔이슈는 라벨 `theme:<id>`로 연결한다(§4에서 발행 시 부여). 롤업은 GitHub
상태에서 계산되므로 손목록이 없다. 파생 데이터(`roadmap.json`·`weekly.json`)는
생성기(consumer repo의 `gen-roadmap.mjs`·`gen-weekly.mjs`)가 매일 크론으로
재생성해 커밋한다 — **절대 손으로 고치지 않는다.** 화면(`/members/roadmap`·
`/members/weekly`)은 그 파생 데이터를 비출 뿐이다.

시각화(공지 마크다운)는 `announce.py`가 담당한다(§Announcement mode, SKILL).

---

## 9. 검증 — 루프도 테스트 대상이다

| 도구 | 무엇을 검증 | 실패 시 |
|---|---|---|
| `scripts/weekly-harness/check.py` | cycle 라벨이 붙은 모든 open 이슈에 `ETA:` 라인 + `Owner`/담당 섹션이 있고, ETA ≤ cycle 날짜 | non-zero exit + 위반 목록 |
| `scripts/weekly-harness/check.py` | cycle 라벨이 붙은 모든 **closed** 이슈에 `Evidence:` 퍼머링크 또는 명시적 면제(§6.1)가 있는가 | non-zero exit + 위반 목록 |
| `scripts/weekly-harness/burndown.py` | (검증 아님) pre-meeting 번다운 리포트 생성 | gh 실패 시 non-zero |
| harness `tests/run.sh` T-V13 | weekly-loop 자산군이 harness에 온전히 등록돼 있는가 | 게이트 실패 |

```bash
# 위반이 있으면 exit 1
python3 scripts/weekly-harness/check.py --cycle weekly-2026-07-21
```

### 9.1 지금 실제로 자동화된 것과 아닌 것

과장하지 않기 위해 정확히 적는다.

| | 상태 |
|---|---|
| 규칙이 코드로 존재하고 non-zero로 실패하는가 | **그렇다** (open: Owner/ETA · closed: Evidence) |
| 테스트로 덮여 있는가 | **그렇다** (`tests/weekly_loop/`, CI에서 pytest로 실행) |
| **누가 실행하는가** | **매일 자동으로 돈다.** `repo-governance live audit` 워크플로의 `weekly-loop-gate` job (03:17 UTC, `workflow_dispatch`로 수동 실행 가능) |
| 게이트 코드 자체는 누가 지키는가 | PR CI(`test.yml` → `repo-governance` job)가 고정 코퍼스로 재현 — 주입한 위반 19건 검출 · clean 16건 통과가 정확히 맞아야 머지된다 |
| 우회로 | `--skip-evidence-gate`는 `CI` / `GITHUB_ACTIONS` / `HYPEPROOF_ENFORCE`가 켜져 있으면 **exit 2로 거부된다.** 로컬 분류 작업 전용 |

**소급 적용 문제는 라벨이 아니라 시각으로 풀었다.** 2026-07-22 00:00 KST 이전에
닫힌 이슈는 `closedAt` 기준으로 자동 면제되므로 과거 이슈를 손댈 필요가 없다.
게이트는 그 시각 이후에 닫히는 이슈부터 적용된다.

**아직 안 하는 것 — `wip` 선점(§6.0)은 규약일 뿐 집행이 아니다.** `wip`을
읽는 검사기는 없다. `check.py`도 `audit.py`도 이 라벨을 모른다. 선점하지 않고
시작한 PR, 남의 선점을 무시하고 만든 PR, 24시간 지난 죽은 `wip` — 어느 것도
CI를 red로 만들지 않는다. 지금 유일하게 기계로 강제되는 지점은 hypeprooflab의
크론 picker(`run-job-parallel.sh`의 skip-label 목록)뿐이고, 그건 이 repo 밖이다.
집행으로 올리려면 별도 이슈로 다룬다 — 그 전까지는 **규약이라고만 적는다.**

**아직 안 하는 것 — URL 실재 검증.** 게이트는 `Evidence:` URL을 열어보지
않는다. GitHub이 기록한 closing PR이 있는 경우만 `existence verified by
GitHub`으로 찍히고, 나머지는 `syntax_valid, existence_unverified`로 구분해
보고한다. 없는 PR 번호를 가리키는 형식만 맞는 링크는 여전히 통과한다.

---

## 10. 자주 묻는 것

**Q. 회의에서 안 나온 일도 cycle 라벨을 붙여야 하나?**
A. 이번 주 안에 끝낼 팀 작업이면 붙인다. 번다운에 잡히는 게 이득이다.
개인 실험/스파이크는 자유 — 단, 산출물이 생기면 원칙 4는 지킨다.

**Q. ETA를 못 지킬 것 같으면?**
A. ETA 전에 이슈에 코멘트 + 다음 cycle로 이관(라벨 교체, ETA 재설정).
월요일 회의에서 "왜 밀렸나"가 아니라 "언제 알았나"를 본다.

**Q. 큰 작업(2주+)은 어떻게 쪼개나?**
A. "다음 월요일에 보여줄 수 있는 1차 산출물"을 먼저 이슈로 만든다 —
설계 문서, 프로토타입, 조사 보고서 등. 나머지는 그 산출물을 본 뒤 다음
cycle에서 다시 이슈화한다 (원칙 2).

**Q. 두 세션이 같은 이슈를 잡으면?**
A. 먼저 `wip`을 단 쪽이 임자다. 나중에 본 쪽은 그 이슈를 놓고 다른 걸 집는다
(§6.0). 이미 둘 다 작업을 시작해 버렸으면 늦게 시작한 쪽이 이슈에 코멘트로
알리고 접는다 — 두 개의 PR을 끝까지 만들어 하나를 버리는 게 제일 비싸다.
claim 코멘트가 24시간을 넘겼으면 죽은 선점이니 회수 코멘트를 남기고 가져간다.

**Q. 라벨을 잘못 만들었으면?**
A. `gh label delete weekly-<잘못된 날짜> --repo jayleekr/<repo>` 후 다시.
라벨 형식은 `weekly-YYYY-MM-DD` — 항상 **다음 회의 날짜**다.

---

*이 문서는 `hypeproof-harness`에서 자동 vendoring된다. 수정 시 PR을 그 쪽으로.*
