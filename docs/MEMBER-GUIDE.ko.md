# HypeProof 멤버 온보딩 가이드

> 이 문서는 **HypeProof 멤버 누구나 따라할 수 있게** 한글로 적힌 가이드다.
> Claude Code, Codex, OpenClaw 같은 코딩 에이전트와 함께 쓰는 걸 가정한다.
> *"이 문서 따라 진행해줘"* 라고 에이전트에게 말하면 각 단계가 실제 명령으로
> 실행된다.

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

**Python 도구를 쓸 거라면**: `hype-pr`로 PR을 만들거나 harness의 policy 스크립트를
돌리려면 PyYAML이 필요하다. harness checkout에서:

```bash
python3 -m pip install -r requirements.txt
```

CI가 검증하는 버전은 Python 3.11+다. consumer 저장소가 아니라 **harness
checkout의 인터프리터**에 설치해야 한다(`docs/HYPE-PR.ko.md` → "실행 전제").

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

## 1.5 계정 & 권한

> 새 멤버라면 공개 온보딩 체크리스트가 가장 빠른 입구다:
> **https://hypeproof-ai.xyz/onboarding**

**모든 active 멤버는 아래 서비스 전부에 접근한다 — "전원이 배포 가능"이
기본 정책이다 (2026-07-07 합의).** 새 멤버가 조인하면 메인테이너(Jay 또는
에이전트)가 초대를 일괄 발송한다. 새 멤버가 할 일은 **초대 수락**뿐이다.
GitHub 아이디와 이메일(Gmail 권장 — Vercel/Supabase 가입에 같은 주소 사용)만
Jay에게 알려주면 된다.

| 서비스 | 무엇에 쓰나 | 권한 | 수락 방법 |
|---|---|---|---|
| **GitHub** (`jayleekr/*` 4개 repo) | 코드 · PR · 머지 | Write | GitHub 알림/이메일에서 초대 4건 수락 |
| **Vercel** (`hype-proof-lab` 팀) | web(hypeproof-ai.xyz) 등 프론트 배포 | Developer | 이메일 초대 수락 (같은 이메일로 Vercel 가입) |
| **HypeProof 포털** (`hypeproof-ai.xyz/members`) | 멤버 문서·재무·기회 보드 | 접근 원장 등재 (admin 이 `/members/admin` 에서) | 등재된 이메일로 로그인 |
| **Supabase** (`sediment` org) | Sediment 프로덕션 Postgres 직접 조회 | Developer | 이메일 초대 수락 — **DB 를 직접 봐야 하는 사람만** |
| **Cloudflare** | Studio Worker(api.hypeproof-ai.xyz) 런타임 | **계정 권한 불필요** | repo write 만 있으면 Actions 로 배포된다 (아래) |
| **Fly.io** | Sediment 백엔드 런타임 | **계정 권한 불필요** | repo write 만 있으면 Actions 로 배포된다 (아래) |

### 배포 자격증명은 사람이 아니라 CI 가 갖는다

Cloudflare 와 Fly 는 **개인 계정 초대도 개인 토큰도 주지 않는다.** 배포 토큰은 repo
시크릿에 하나만 있고, 배포는 GitHub Actions 가 한다.

| 대상 | 워크플로 | 어떻게 |
|---|---|---|
| Studio Worker (Cloudflare) | `hypeproof-studio` → `deploy-worker.yml` | Actions 탭에서 **Run workflow** → **`dry_run` 체크를 끈다** (기본값 `true` 는 테스트·타입체크만 하고 배포하지 않는다) |
| Sediment 백엔드 (Fly) | `sediment` → `fly-deploy.yml` | 런타임 경로(`services/sediment/{applications,lab_lib,lab_platform,scripts,prompts,data,config}`, `pyproject.toml`, `uv.lock`, `Dockerfile`, `infra/deploy/`)가 바뀐 `main` 머지만 자동 배포 · 그 밖의 변경(프론트·테스트·문서만)은 배포되지 않으니 필요하면 Run workflow |

**즉 "배포할 수 있는 사람 = repo 에 write 가 있는 사람"** 이다. 새 멤버에게 따로 해줄
것이 없고, 회수는 collaborator 제거 하나로 끝나며, 누가 언제 배포했는지가 Actions 실행
기록에 남는다. 사람마다 토큰을 쥐여주면 이 셋이 전부 깨진다.

write 는 멤버마다 repo 별로 따로 준다. 2026-09-15 기준 `hypeproof-studio` 는 멤버 전원이
write 지만 `sediment` 는 **TJ-kr 이 read 뿐**이라 Sediment 배포를 실행할 수 없다. 내 권한은
repo 의 Settings 가 아니라 `gh api repos/jayleekr/<repo>/collaborators/<내 id>/permission` 으로
확인하고, `read` 로 나오면 Jay 에게 **그 repo 의 write** 를 요청한다 (계정 토큰이 아니다).

**Studio Worker 는 프로덕션이다. 수업 중에는 배포하지 않는다.** 워크플로가 진행 중인 수업을
감지하면 멈춘다. `override_live_session` 은 그 판단을 일부러 뒤집는 입력이고 실행 요약에
기록된다 — 켜기 전에 강사에게 먼저 묻는다.

> 이 표는 2026-08-19 까지 두 줄 다 "Jay 계정 경유 · 직접 배포가 필요해지면 Jay에게 요청"
> 이라고 적혀 있었다. **위임은 CI 로 이미 끝나 있었고 문서만 안 따라왔다.** 그래서 멤버가
> 계속 Jay 에게 권한을 요청했고, 하마터면 개인 Cloudflare 계정 토큰을 발급할 뻔했다.
> 위임을 코드로 해놓고 문서에 안 적으면 위임이 안 된 것과 같다.

**계정 자체를 봐야 하는 일**(대시보드 확인, 시크릿 추가, DNS)은 여전히 Jay 다. 그건
권한 문제가 아니라 **계정 소유자가 Jay 개인** 이기 때문이고, 별건으로 다룬다.

메인테이너 체크리스트 (새 멤버 조인 시):

1. GitHub: `gh api -X PUT repos/jayleekr/<repo>/collaborators/<github-id> -f permission=push` × 4개 repo (hypeprooflab · hypeproof-studio · sediment · hypeproof-harness)
2. Vercel: `hype-proof-lab` 팀 → Members → Invite (Developer)
3. **접근 원장: `hypeproof-ai.xyz/members/admin` → 이메일·displayName·역할 등재**
4. `hypeprooflab/data/members.json`에 멤버 엔트리 추가 (PR)
5. Discord 채널 초대
6. Supabase: `sediment` org → Team → Invite (Developer) — **Sediment DB 를 직접 봐야 하는 사람만**

> **3번이 빠지면 그 사람은 `hypeproof-ai.xyz/members` 에 못 들어간다.** 4번(로스터)만
> 해두면 `/creators` 에는 보이는데 로그인은 안 되고, 화면 어디에도 이유가 안 뜬다.
> 2026-08-17 에 이 목록에 3번이 없어서 세 명이 그 상태였다 — 본인이 말하기 전까지 몰랐다.
>
> 6번(Supabase)은 **멤버 관리 목적으로는 필요 없다.** 접근 원장은 3번의 화면에서 고치고,
> 그 org 권한은 다른 테넌트의 대화·과금 기록까지 함께 여는 넓은 문이다.
>
> 전부 됐는지는 hypeprooflab 에서 한 번에 확인한다 (다섯 축 대조):
> ```bash
> APP_DB_URL=<dsn> python3 scripts/members_audit.py
> ```

### 머지 정책 (2026-07-07부터)

모든 repo(studio · sediment · harness)에서 **PR 리뷰 승인 요건이 제거**됐다.
PR + required CI checks가 green이면 누구든 (자기 PR 포함) 바로 머지한다.
CODEOWNERS는 전원 등록된 리뷰 요청 라우팅용으로만 남아 있다. 리뷰는 여전히
권장 — §4.3a의 lens 리뷰 문화는 유지된다.

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
6. vendored `skill-creator` + `MEMBER-GUIDE.ko.md` + `AGENT-GUIDE.ko.md` 존재 확인
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
`docs/MEMBER-GUIDE.ko.md`(이 파일), `docs/AGENT-GUIDE.ko.md`, 루트
에이전트 진입점(`CLAUDE.md`, `AGENTS.md`, `OPENCLAW.md`)에 vendoring돼 있다.

```bash
cd "$WS/<your-repo>"     # 본인 워크스페이스 경로
claude
# 일상 워크플로 — §4 참조
```

---

## 2d. 에이전트별 진입점

consumer repo 루트에는 에이전트별 얇은 진입점이 들어간다.

| 파일 | 대상 | 역할 |
|---|---|---|
| `CLAUDE.md` | Claude Code | Claude가 repo 규약을 찾는 첫 파일 |
| `AGENTS.md` | Codex | Codex가 repo 규약을 찾는 첫 파일 |
| `OPENCLAW.md` | OpenClaw | OpenClaw용 repo 진입점 |

세 파일 모두 공통 규칙을 복사하지 않고 `docs/AGENT-GUIDE.ko.md`를 가리킨다.
기존 repo별 지침이 있으면 그 내용은 보존하고, 공통 가이드 링크만 추가한다.
repo별 세부 규칙은 각 consumer의 `README.md`, `DEV-GUIDE.md`, `docs/` 또는
해당 에이전트 진입점에 둔다.

---

## 3. 어떤 스킬이 있나

`.claude/skills/` 안을 보면 그 repo가 쓰는 Claude Code 스킬들이 있다.

**전 repo 공통(vendored from harness)**:

- `skill-creator` — 새 스킬을 만들고 고치고 평가하는 generic 툴킷.
  Claude Code에서 `/skill-creator` 호출.
- `hype-review` — 내게 온 PR 리뷰 요청을 확인하고, 사용자별 기본 lens로
  질문지와 작성자 답변 가이드를 만든다. Claude Code에서 `/hype-review` 호출.

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
이슈 발행·선점 → 브랜치 → 커밋·테스트 → PR(템플릿) → merge → 이슈 auto-close
```

### 4.1 이슈 먼저

모든 변경은 GitHub 이슈에서 시작. UI에서 발견한 거면 studio의
`/report-ui` 스킬 사용. 그 외엔 그 repo의 GitHub 이슈 폼.

> **휴먼 vs AI 구분**: 이슈에 `human-needed` 라벨이 있으면 사람이 처리해야
> 함. 라벨 없으면 AI 에이전트가 자동 해결 시도 가능. 라벨 기준은
> **지용(JiWoong) 소유, 5/21 마감**.

### 4.1a 작업 선점 (claim)

브랜치를 파기 전에, 잡았다는 걸 이슈에 남긴다. 사람 여러 명 + Claude Code
세션 여러 개 + 크론 루프가 같은 이슈 목록을 본다. worktree를 나눠도 그건
**파일 충돌**만 막는다 — **중복 작업**은 못 막는다.

```bash
gh issue edit <N> --repo jayleekr/<repo> --add-label wip --add-assignee @me
gh issue comment <N> --repo jayleekr/<repo> \
  --body "claim: <세션 이름> / branch: <브랜치 이름>"
```

| 단계 | 규칙 |
|---|---|
| **선점** | 작업 **시작 전에** `wip` 라벨 + assignee + 세션·브랜치를 밝힌 코멘트 |
| **존중** | `wip`이 붙은 이슈는 집지 않는다. 사람도, 에이전트도 |
| **해제** | 끝나면 `wip` 제거. `Closes #<N>` PR이 머지되면 자동으로 풀린다 |
| **만료** | claim 코멘트가 **24시간** 넘은 `wip`은 죽은 선점 — 회수 코멘트를 남기고 다시 잡아도 된다 |

라벨만 달고 코멘트를 빼면 안 된다. 라벨에는 시각이 없어서 24시간 만료를
판정할 근거가 없다. 코멘트 없는 `wip`은 처음부터 죽은 선점으로 본다.

> **집행이 아니라 규약이다.** `wip`을 검사하는 CI는 없다. 선점 없이 올린 PR도,
> 이중 선점도 아무 체크를 red로 만들지 않는다. 이슈를 닫을 때의
> `Evidence:` 게이트(WEEKLY-LOOP.ko.md §6.1)는 코드가 강제하지만, 이건
> 지키기로 한 약속이고 안 지키면 버려지는 PR로 비용을 치를 뿐이다.
> 배경은 WEEKLY-LOOP.ko.md §6.0.

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

**정책: PR 필수, 리뷰 선택.** 자신 있으면 셀프 머지 OK. (2026-07-07부터
harness 포함 모든 repo에서 승인 요건 제거 — CI green이면 셀프 머지 가능.
§1.5 "머지 정책" 참고. 리뷰는 여전히 권장.)

### 4.3a 리뷰 요청을 받으면

리뷰 요청은 가능한 모든 active 멤버에게 간다. 이것은 모든 승인을 기다리라는 뜻이
아니라, 각자 역할 관점으로 질문하고 배우기 위한 기본 알림이다. merge는 branch
protection, CODEOWNERS, required checks가 요구하는 quorum을 따른다.

```bash
# Claude Code에서 내게 온 리뷰 요청 확인
/hype-review

# 특정 PR을 내 기본 lens로 점검
/hype-review https://github.com/jayleekr/<repo>/pull/<number>
```

스킬이 `policy/members.yaml`의 사용자별 기본 lens를 읽어서 공통 질문, 역할별 질문,
작성자에게 남길 approve/comment/request changes 답변 초안을 생성한다. CLI로 직접
실행해야 하는 상황이면 `python3 scripts/hype-review/review.py --mine`을 쓴다.
이번 PR에서 더 봐야 할 lens가 있으면 스킬에 "security 관점도 추가"처럼 요청한다.
자세한 기준은 `docs/HYPE-REVIEW.ko.md`.

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
2. **`CLAUDE.md` / `AGENTS.md` / `OPENCLAW.md`** — 에이전트별 진입점
3. **`docs/AGENT-GUIDE.ko.md`** — 공통 에이전트 규약
4. **Discord** — 채널: `#daily-research`, `#content-pipeline`, `#잡담`
5. **메인테이너 직접 핑**: Jay (`@jayleekr`) / Jehyeong (`@JeHyeong2`)

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
worktree에서 동시 세션 1개 권장. 단, **worktree 분리는 파일 충돌만 막지
중복 작업은 못 막는다** — 두 세션이 각자 다른 worktree에서 같은 이슈를 풀면
PR 두 개가 나오고 한쪽은 버려진다. 시작 전에 `wip`으로 이슈를 선점할 것
(§4.1a).

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
