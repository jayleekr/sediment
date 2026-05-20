# HypeProof 멤버 온보딩 가이드

> 이 문서는 **HypeProof 멤버 누구나 따라할 수 있게** 한글로 적힌 가이드다.
> Claude Code를 쓰는 걸 가정한다. *"이 문서 따라 진행해줘"* 라고 Claude
> Code에게 말하면 각 단계가 실제 명령으로 실행된다.

> **출처(provenance)**: `jayleekr/hypeproof-harness:docs/MEMBER-GUIDE.ko.md`
> 의 vendored 사본. 직접 수정하지 말 것 — 변경은 harness에서 PR로.

---

## 0. 플랫폼 / 사전 조건

| OS | 멤버 온보딩 | studio 로컬 빌드 | sediment/lab 개발 |
|---|---|---|---|
| **macOS** (arm64 권장) | ✓ 전부 동작 | ✓ 정책상 유일 지원 | ✓ |
| **Linux** (x86_64/arm64) | ✓ 대부분 동작 (`stat -f` 의존 메인테이너 스크립트 일부만 BSD 전용) | ✗ METAPLAN §0 정책상 미지원 | ✓ |
| **Windows** | △ **WSL2 강력 권장**. 네이티브 PowerShell/cmd 미지원 | ✗ 미지원 | △ WSL2로 가능 |

**Studio 멤버라면**: 로컬 빌드는 macOS arm64만 가능 (METAPLAN §0). 그 외 OS에선
CI 빌드된 `.exe`/`.dmg`만 받음 — DEV-GUIDE.md 참고.

**모든 멤버**: 워크스페이스 디렉토리는 **본인이 정한다**. 아래 예시는 macOS의
흔한 컨벤션(`~/CodeWorkspace`)이지만 자기 컨벤션(`~/code`, `~/dev`,
`~/projects` 등)을 그대로 써도 됨. `/onboard-member` 스킬이 묻는다.

이 문서에서 `$WS`로 표기되는 자리는 자기 워크스페이스 베이스 경로다. 예:
`$WS=~/CodeWorkspace`이면 `$WS/hypeproof-studio` = `~/CodeWorkspace/hypeproof-studio`.

---

## 1. 너는 어느 repo의 멤버인가?

HypeProof는 4개 repo로 구성된다. **너는 그 중 1~2개에 기여한다.** 어느
repo인지는 회의/Discord에서 배정받았을 것이다.

| Repo | 무엇 | 주 기여자 |
|---|---|---|
| **`hypeproof-studio`** | VSCodium fork 기반 워크숍 도구 | Jay · 진용 · 봉호 · 재형 · TJ |
| **`sediment`** | 지식 DB / SaaS 백엔드·프론트 | Jay · 재형 · 진용 |
| **`hypeprooflab`** | 공개 사이트(hypeproof-ai.xyz) + 콘텐츠/운영 | Jay · 재형 |
| **`hypeproof-harness`** | 공유 스킬/규약 + 온보딩 스킬 — **1회만 clone, 그 후엔 무시** |

`hypeproof-harness`는 처음 한 번 셋업할 때만 쓴다. 그 안의 `/onboard-member`
스킬이 자기 consumer repo를 clone하고 환경을 잡아준다. 그 다음부턴 자기
consumer repo에서만 일한다.

---

## 2. 시작하기 / Setup

### 2a. 1회성 온보딩 (권장 — 자동화)

```bash
git clone git@github.com:jayleekr/hypeproof-harness.git
cd hypeproof-harness
claude            # Claude Code 세션 시작
# 세션에서:
/onboard-member   # 또는 한글로 "온보딩"
```

스킬이:
0. **플랫폼 점검** (`uname -s`로 macOS/Linux/Windows 판정 + Studio 멤버라면
   macOS-only 정책 알림)
1. 어느 consumer repo 멤버인지 물음 (studio/sediment/lab)
2. **워크스페이스 베이스 경로 묻기** (`$HYPEPROOF_WORKSPACE` 환경변수, 또는
   기존 `~/CodeWorkspace` / `~/code` / `~/dev` 등 후보 제시, 본인 입력 가능)
3. `$WS/<repo>`에 clone (`--recursive` 자동 적용 — studio의 vscodium-base
   서브모듈 같이 가져옴)
4. git hooks 설치 (`.githooks/` 있으면 — studio의 pre-push 등)
5. Claude Code `.claude/settings.json` 검증 + MCP 서버 점검
6. vendored `skill-creator` + `MEMBER-GUIDE.ko.md` 존재 확인
7. 이 문서로 안내 후 종료

**키/토큰 자동 저장은 안 한다** — 안내만. 본인이 처리. **재실행 안전**
(idempotent — git pull만).

### 2b. 수동 셋업 (자동화 없이)

자동화 안 쓰거나 자기 셋업 패턴이 다르면 직접:

```bash
# 자기 워크스페이스 베이스 정하기 (자기 컨벤션 그대로)
WS=~/CodeWorkspace         # 또는 ~/code, ~/dev, ~/projects, 어디든

git clone --recursive git@github.com:jayleekr/<your-repo>.git "$WS/<your-repo>"
cd "$WS/<your-repo>"
[ -d .githooks ] && git config core.hooksPath .githooks
```

`<your-repo>`는 `hypeproof-studio`, `sediment`, `hypeprooflab` 중 자기 것.
clone 직후 바로 동작 — 별도 `submodule update --init` 같은 거 없다
(`skill-creator`는 실파일로 들어와 있음, `vscodium-base`만 studio용 서브모듈).

> **Windows 사용자**: WSL2 안에서 위 명령을 그대로 실행. 네이티브 PowerShell은
> 미지원 (rsync·BSD stat 등 의존 도구 부재). Git Bash로도 부분 가능하지만
> 일관성을 위해 WSL2 권장.

### 2c. 일상 작업 — harness는 잊는다

온보딩 후엔 자기 consumer repo에서만 일한다. harness는 다시 clone할 필요
없다. shared 콘텐츠는 이미 `.claude/skills/skill-creator/`와
`docs/MEMBER-GUIDE.ko.md`(이 파일)에 vendoring돼 있다.

```bash
cd "$WS/<your-repo>"     # 본인 워크스페이스 경로
claude
# 일상 워크플로 — §4 참조
```

---

## 3. 어떤 스킬이 있나

`.claude/skills/` 안을 보면 그 repo가 쓰는 Claude Code 스킬들이 있다.

**전 repo 공통(vendored from harness)**:

- `skill-creator` — 새 스킬을 만들고 고치고 평가하는 generic 툴킷.
  Claude Code에서 `/skill-creator` 호출.

**각 repo 자기 스킬** (예시):

- studio: `hype-open-pr`, `report-ui` 등 — PR/이슈 발행 (studio 전용)
- sediment: `curator-validate`, `sediment-connect` 등
- lab: `paper-lab`, `column-workflow`, `roadmap-review` 등 다수

자기 repo의 `.claude/skills/` 디렉토리를 한 번 둘러보길 권한다.

---

## 4. 같이 일하는 흐름 — 5단계

회의 2026-05-18에서 합의한 워크플로우. **모든 코드 변경이 이 길로만
`main`에 들어간다.**

```
이슈 발행 → 브랜치 → 커밋·테스트 → PR(템플릿) → merge → 이슈 auto-close
```

### 4.1 이슈 먼저

모든 변경은 GitHub 이슈에서 시작. UI에서 발견한 거면 studio의
`/report-ui` 스킬 사용. 그 외엔 그 repo의 GitHub 이슈 폼.

> **휴먼 vs AI 구분**: 이슈에 `human-needed` 라벨이 있으면 사람이 처리해야
> 함. 라벨 없으면 AI(Claude Code)가 자동 해결 시도 가능. 라벨 기준은
> **지용(JiWoong) 소유, 5/21 마감**.

### 4.2 브랜치 이름

| 종류 | 이름 |
|---|---|
| 버그 픽스 | `fix/issue-<N>-<slug>` |
| 새 기능 | `feat/issue-<N>-<slug>` |
| 문서 | `docs/issue-<N>-<slug>` |
| 기타 | `chore/<주제>` |

```bash
git switch -c fix/issue-12-something
```

> **`main`에 직접 push 금지.** 메인테이너(Jay) 전용. 가드:
> `.githooks/pre-push` + CI `main-guard` (소프트 — 우회는 빨간 빌드).

### 4.3 PR 만들기

**studio면**: Claude Code에서 `/hype-open-pr` 스킬 실행. 브랜치 확인, push,
PR 본문 대화형으로 채워준다.

**그 외 repo**: `gh pr create --fill --base main`

**정책: PR 필수, 리뷰 선택.** 자신 있으면 셀프 머지 OK. (단 harness
repo 변경은 항상 피어리뷰 — 메인테이너 영역이라 멤버는 손 댈 일 없음.)

### 4.4 PR 본문 필수

- `Closes #<N>` — 머지 시 이슈 자동 close
- **What & why** — 한 단락
- **Tested** — 무엇이 통과했는지 (자기 repo 테스트 게이트 참고: studio
  `e2e/`, sediment `make validate-*`, lab `qa`/`healthcheck`)

### 4.5 머지 후

- 브랜치 삭제: `gh pr merge <PR#> --squash --delete-branch`
- 이슈는 auto-close됨
- 자기 worktree에서 `git switch main && git pull` 동기

---

## 5. 병행 작업 — claude -w

이슈 여러 개 동시 진행할 땐 **Claude Code 네이티브 worktree** 사용:

```bash
claude -w issue-12         # 이슈 #12용 새 worktree + 세션
claude -w issue-15 --tmux  # 또 다른 이슈를 별도 worktree(+tmux 패널)
```

흐름은 §4와 동일. worktree 디렉토리는 `.claude/worktrees/`(gitignored)에
두면 깔끔.

각 repo는 자기 함정이 있다 (studio는 port 8787, sediment는 Fly proxy 등).
자기 repo `DEV-GUIDE.md`의 worktree 섹션 참고.

---

## 6. 가드레일 — 어겨선 안 됨

- **시크릿 절대 커밋 금지.** 키·토큰·`.env`·`.dev.vars` 등. 새면 Jay에게
  알리고 로테이션 — 드라마 X.
- **`main` 직접 push 금지** (메인테이너 제외). PR로만.
- **스킬은 `/skill-creator`로만** 만들고 고친다. 직접 SKILL.md를 손으로
  쓰지 말 것.
- **vendored 파일은 직접 수정 금지**. `.claude/skills/skill-creator/`에는
  `HARNESS_VERSION` 파일이 있다. 그 디렉토리는 harness 캐노니컬의 복사본
  — 수정하려면 harness에 PR을 해야 한다 (대부분의 멤버는 손댈 일 없음).

---

## 7. 모르는 거 있으면

1. **자기 repo의 `DEV-GUIDE.md`** (studio는 풍부, 다른 repo는 README) —
   stack-specific 함정·빌드 키 등
2. **`.claude/CLAUDE.md`** (있는 repo는) — repo별 규약·가이드라인
3. **Discord** — 채널: `#daily-research`, `#content-pipeline`, `#잡담`
4. **메인테이너 직접 핑**: Jay (`@jayleekr`) / Jehyeong (`@JeHyeong2`)

모르는 개념(이슈/PR/브랜치)이 있으면 **즉시 물어봐**. 회의 룰: "모르는
거 적지 말고 그 자리에서 물어봐"(24:19 Jay). 적어놓고 나중에 혼자
공부하는 건 동기화 안 됨.

---

## 8. 자주 묻는 것

**Q. harness repo도 clone해야 하나?**
A. **1회만** — 처음 온보딩 시(`/onboard-member` 스킬). 그 다음엔 안 쓴다.
자동화 안 쓸 거면 자기 consumer repo만 직접 clone해도 동작은 한다 (§2b).

**Q. `.claude/skills/skill-creator/HARNESS_VERSION` 파일은 뭐냐?**
A. 그 vendored 스킬이 harness 어느 commit에서 복사됐는지의 증거. 손대지
말 것. drift 검출용.

**Q. 스킬이 바뀌었으면 어떻게 받나?**
A. 메인테이너(Jay)가 harness에서 업데이트 → sync 스크립트로 너의 consumer
main에 commit 들어옴 → `git pull` 하면 자동 반영. 너가 따로 할 일 없음.

**Q. PR 리뷰 받아야 하나?**
A. 룰: 필수 아님(자신 있으면 셀프 머지). 하지만 첫 몇 개는 메인테이너에게
리뷰 부탁 권장 — 워크플로우 익숙해질 때까지.

**Q. 클로드 코드 세션을 여러 개 띄워도 되나?**
A. 자유. `claude -w issue-N`으로 worktree 단위로 분리하는 게 깔끔. 같은
worktree에서 동시 세션 1개 권장.

---

## 9. 추가 자료

- 회의 기록: 2026-05-18 Weekly (Discord 핀)
- 토폴로지/시퀀스/마일스톤:
  `hypeprooflab:jay/reports/2026-05-19-repo-structure-diagram.html`
- Vendor 마이그레이션 보고서 (2026-05-20):
  `hypeprooflab:jay/reports/2026-05-20-vendor-migration.html`
- 16 Essences(제품 철학): `hypeproof-studio:docs/essence-v0.1.md`

---

*이 문서는 `hypeproof-harness`에서 자동 vendoring된다. 수정 시 PR을 그 쪽으로.*
