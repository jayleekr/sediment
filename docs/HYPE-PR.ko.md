# hype-pr

> 작성일 2026-09-08 · 상태: 활성

> 이 문서는 `hypeproof-harness:docs/HYPE-PR.ko.md`가 원천이다.
> 제품 repo에 vendoring된 사본은 직접 고치지 말고 harness에서 PR로 바꾼다.

---

## 목적

`hype-pr`는 PR 생성 시 팀 운영 규칙을 사람 기억에 맡기지 않기 위한 하네스다.

- reviewer 요청은 기본으로 하지 않는다. 사용자가 특정 PR의 peer review를 명시한
  경우에만 작성자를 제외한 active 멤버 전원을 요청한다. CODEOWNERS의 catch-all이
  PR 생성 또는 draft의 ready 전환과 함께 자동 요청한 reviewer도 기본 경로에서는
  즉시 제거한다.
- auto-merge는 켜도 되는 PR인지 먼저 판정한다.
- 보안, 배포, 데이터, dependency, governance 변경은 auto-merge 대상에서 제외한다.
- 실제 merge는 여전히 branch protection, CODEOWNERS, required checks가 통과해야 한다.

auto-merge는 필수 보호 조건이 모두 통과하면 GitHub가 순서대로 merge하게 예약하는
기능이다. reviewer 요청 여부와는 독립적으로 판정한다.

---

## 실행 전제

`scripts/hype-pr`와 그것이 불러오는 `scripts/repo-governance`는 `policy/*.yaml`을
읽으므로 **PyYAML**이 필요하다. 선언은 harness 루트의 매니페스트에 있다.

```bash
python3 -m pip install -r requirements.txt       # 도구 실행
python3 -m pip install -r requirements-dev.txt   # 테스트까지
```

CI가 검증하는 버전은 **Python 3.11+**이다(`.github/workflows/test.yml`).

의존성은 **harness checkout의 인터프리터**에 있어야 한다. consumer 사본의
`scripts/hype-pr/pr.py`는 `policy/repos.yaml`이 없으면 canonical harness로
`os.execv` 위임하고, `yaml` import는 벤더 대상이 아닌 `scripts/repo-governance/`
에서 일어나기 때문이다. consumer 저장소에 의존성을 추가해도 해결되지 않는다.

PyYAML이 없으면 명령이 어느 인터프리터에 무엇을 설치해야 하는지 알려주며 중단한다.

---

## Agent가 PR을 만드는 흐름

Harness, Lab, Studio, Sediment의 PR 생성은 `skills/hype-pr/SKILL.md` 정본을 따른다. consumer에서는
`.claude/skills/hype-pr/SKILL.md`로 배포되고 `.agents/skills/hype-pr/`도 같은 내용을 가리킨다.
설치 대상은 `scripts/hype-pr/install.py`의 `CONSUMER_REMOTE`가 정하며 `tests/consumers.txt`와
같은 집합이어야 한다. 단 **preparation 게이트가 걸리는 저장소는 이와 별개**로
`policy/change-impact.json`의 `repositories` 목록이다. Sediment는 설치 대상이지만 아직 이
목록에 없어 `create`가 preparation 없이 진행된다(`pr.py`가 대상 repo로 게이트 여부를 정한다).
Sediment에 게이트를 걸려면 먼저 그 repo에 `config/traceability.json`을 두고 오너가 정해야 한다.
개발 시작에 기준 연결을 잡고, commit 후 `inspect`로 대상·상위·하위와 미등록 변경을 읽는다.
Agent가 실제 문서를 읽고 assessment를 작성하며, 별도 모델 API key는 필요 없다.
브랜치를 만들기 전 해당 PR 크기의 수직 결과와 인수 조건을 담은 같은 repo의 열린
실행 이슈를 만들거나 재사용한다. Epic은 부모 제품 결과이며 PR의 close target이 아니다.
`create --issue`는 이슈의 존재·열림·repo·PR/Epic 여부와 본문의 close target 일치를
검사한다. 구현과 이슈가 의미상 같은 범위인지는 자동 판정하지 않으며 작성자와 조정
에이전트가 확인한다.
`prepare`는 repo/base/head/다른 정본 repo SHA/정책·도구 version에 묶인 receipt를 git metadata에
저장한다. `create --apply`는 이 receipt를 재검사하고 remote head까지 일치해야 생성한다.
새 owner/상위 단계 누락은 막고 기존 debt는 별도로 표시한다. 미등록 구현은 REQ와 test에 연결하며,
새 기준 문서를 supporting/edit-only로 위장하지 않는다. semantic 판단과 test evidence는 Agent의
attestation이다. 이 과정을 독립 human approval 또는 URL/테스트 결과 자체의 자동 검증이라고 부르지 않는다.

```bash
python3 scripts/hype-pr/pr.py inspect --repo jayleekr/hypeprooflab \
  --output /tmp/impact.json --assessment-template /tmp/assessment.json
# Skill 지침에 따라 assessment를 작성한다. 원문/상세 reasoning은 public PR에 복사하지 않는다.
python3 scripts/hype-pr/pr.py prepare --repo jayleekr/hypeprooflab --assessment /tmp/assessment.json
# 출력된 receipt path를 아래 create --preparation 에 전달한다.
```

consumer 명령은 sibling Harness checkout 또는 `HYPEPROOF_HARNESS`로 정본 명령에 위임한다.
Harness가 없으면 명시적으로 실패한다. 정책·엔진 복제나 fallback owner 명단은 없다.
원격 기준 commit이 로컬에 없으면 `git fetch origin main` 후 다시 실행한다. source를 읽을 권한이
없으면 권한을 복구한다. 코드 변경/새 commit/base·다른 repo의 전진은 다시 검토할 이유다.
`create`의 `--path`는 dry-run 참고용이며 실제 생성의 위험 판정은 git diff로 계산한다.

## Work GitHub transport

Work에서 `gh` 인증을 사용할 수 없으면 연결된 GitHub 도구가 로컬 CLI의 요청을
수행한다. 토큰을 셸·파일에 복사하지 않는다. `scripts/hype-pr/work_host.js`의
`runWorkCommand(tools, options)`를 Work code mode에서 실행한다. 반환값은 종료 코드,
비공개 stdout/stderr 디렉터리, 생성한 PR 식별자와 부가 작업 오류다.

options는 다음을 지정한다.

- `harness`: 정본 Harness checkout 절대 경로. CLI와 host 모두 이 버전을 사용한다.
- `checkout`: 작업 repo의 깨끗하고 커밋된 worktree 절대 경로.
- `args`: 기존 CLI 인수 배열. `inspect` → Agent assessment 작성 → `prepare` →
  `create --preparation ... --apply`를 동일하게 실행한다. consumer PR에도 같은 경로를 쓴다.
- `repositories`: `policy/change-impact.json`의 저장소 목록. 임의로 확대하지 않는다.
- `repo`, `branch`, `author`: 사용자 작업 대상과 연결된 GitHub 계정.
- `reviewers`: 명시적인 `--request-reviewers`를 사용할 때 허용할 active 비작성자 목록.
- `allowCreate`: inspect/prepare는 false, 사용자가 요청한 PR 생성은 true.
- `onProgress`: 선택적인 진행 알림 함수. 원문이나 평가 내용을 출력하지 않는다.

host 소스를 읽어 `runWorkCommand` 함수를 로드하고 현재 연결의 `tools` 객체를 전달한다.
Python CLI는 임시 0700 디렉터리의 요청/응답으로 통신한다. 응답은 원자적으로 전달되고,
60초 무응답·응답 ID 불일치·도구 오류는 실패한다. immutable contents만 같은 host 세션에서
재사용하며 main/head는 매번 다시 읽는다. 생성 직전 source SHA와 연결 계정도 재확인한다.
원격에 커밋이 없으면 GitHub 연결로 같은 파일 tree를 feature branch에 저장한 뒤 해당
원격 commit을 로컬로 fetch/checkout하고 검토한다. 다른 SHA를 같은 준비 기록으로 취급하지 않는다.

Work transport는 PR 생성, 기본 reviewer 정리, 명시적으로 선택한 reviewer/label 요청만 쓰기 지원한다. auto-merge,
기존 PR 변경, impact scan/checkpoint 쓰기는 이 경로의 범위 밖이다. 응답 유실은 생성 실패를
뜻하지 않으므로 원격 PR부터 확인한다. 테스트나 host 종료 후 임시 원문은 보존 정책에 맞게 정리한다.
이 모드는 검증 로직을 우회하거나 독립 사람 승인을 부여하지 않는다.

검증: `python -m pytest tests/hype_pr tests/change_impact -q`와
`node --test tests/hype_pr/work_host.test.js`. 실제 사용은 연결된 세 저장소 읽기와
guarded PR 생성으로 별도 확인한다. 사용자에게 Work transport 구현을 승인받은 경우
개발 브랜치의 host/CLI로 해당 변경 자체의 PR 준비를 시험할 수 있다.

## 기본 사용법

먼저 dry-run으로 auto-merge eligibility를 확인한다. reviewer 목록은 기본으로 비어 있다.

```bash
python3 scripts/hype-pr/pr.py plan \
  --repo jayleekr/hypeproof-studio \
  --author JinyongShin \
  --path docs/dev/cohort.md \
  --auto-merge
```

사용자가 특정 PR의 리뷰를 명시한 경우에만 기존 PR에 reviewer를 요청한다.

```bash
python3 scripts/hype-pr/pr.py request-reviewers \
  --repo jayleekr/sediment \
  --pr 87

# 실제 GitHub mutation
python3 scripts/hype-pr/pr.py request-reviewers \
  --repo jayleekr/sediment \
  --pr 87 \
  --apply
```

새 PR을 만들 때도 기본은 dry-run이며 reviewer 요청은 없다. dry-run의
`reviewer_cleanup_commands`는 CODEOWNERS가 자동으로 붙일 수 있는 요청의 제거 계획이다.

```bash
python3 scripts/hype-pr/pr.py create \
  --repo jayleekr/hypeproof-harness \
  --head feat/hype-pr-workflow-harness \
  --issue 123 \
  --title "Add hype-pr workflow harness" \
  --body-file /tmp/pr-body.md \
  --author jayleekr \
  --path scripts/hype-pr/pr.py \
  --path docs/HYPE-PR.ko.md
```

해당 PR에 peer review가 명시적으로 필요할 때만 `--request-reviewers`를 추가한다.

PR 본문은 `--issue`에 지정한 이슈만 `Closes`/`Fixes`/`Resolves`로 닫아야 한다.
`--preparation <prepare 출력 경로> --apply`를 붙이면 이슈와 검토를 재확인한 뒤 `gh pr create`를 실행한다. `--auto-merge`를 같이 붙였고
eligibility가 통과하면 생성 직후 `gh pr merge --auto --squash --delete-branch`도
실행한다.

`--request-reviewers`를 사용하면 PR 생성 후 1명씩 요청한다. 특정 repo에서 아직 write 권한이 없거나
초대를 수락하지 않은 멤버가 있으면 PR 생성 자체를 막지 않고, 해당 reviewer 요청
실패를 JSON 결과에 남긴다.

---

## Auto-Merge 판정

auto-merge는 다음 조건을 모두 만족해야 eligible이다.

- CLI에서 `--auto-merge`를 명시했다.
- PR이 draft가 아니다.
- repo profile의 `repository.allow_auto_merge`가 true다.
- repo inventory에 required status checks가 선언돼 있다.
- 변경 파일에서 high-risk category가 감지되지 않았다.
- PR label에 `human-needed`, `security`, `deploy`, `data`, `incident`,
  `breaking-change`, `do-not-merge`가 없다.

현재 active development profile은 auto-merge를 허용한다.

| Profile | GitHub auto-merge setting | 이유 |
|---|---:|---|
| `harness-core` | true | required review/check가 가장 엄격하므로 조건 통과 후 예약 merge 허용 |
| `public-product` | true | public code/private authority 모델에서 보호 조건 통과 후 예약 merge 허용 |
| `private-product` | true | private 예외 repo도 branch protection 통과 후 예약 merge 허용 |
| `content-vault` | false | vault/secret 성격 변경은 수동 merge 유지 |
| `release-artifact` | false | release repo는 source workflow가 artifact를 발행하고 사람 개발 PR은 예외 |

high-risk path는 auto-merge를 막는다.

| Risk | 예시 |
|---|---|
| `security` | `auth`, `admin`, `oauth`, `secret`, `token`, `credential`, `SECURITY.md` |
| `deploy` | `.github/workflows/`, `vercel`, `fly.toml`, `wrangler`, `deploy`, `release` |
| `data` | `migration`, `schema`, `tenant`, `rls`, `database`, `.sql` |
| `dependency` | lockfile, `pyproject.toml`, requirements, `Cargo.lock` |
| `governance` | `policy/`, `CODEOWNERS`, branch protection, repo-governance |

docs/UI 변경은 high-risk가 아니지만, `human-needed`가 붙으면 auto-merge가
막힌다.

---

## 릴리즈 노트 규약

배포 알림은 커밋마다 영어로 흩뿌리지 않고, **매일 06:00(KST) 한글 다이제스트**
하나로 모아 Discord에 올리고 `/members/releases`에 전체 changelog로 쌓는다
(hypeprooflab `web/scripts/gen-releases.mjs` + `releases-refresh.yml`). 그 다이제스트의
품질은 **PR 제목**에서 나온다. 그래서 PR 제목은 규약을 지킨다.

**제목 형식** — `type(scope): 한글 요약`

- `type`은 conventional-commit 타입. 다이제스트가 이걸로 카테고리를 나눈다.
- `scope`는 선택(예: `members`, `auth`, `cron`). 다이제스트에 함께 표시된다.
- 요약은 **한글로, 배포 대상이 읽고 뭐가 바뀌었는지 알 수 있게** 한 줄.
- breaking change는 `type(scope)!:` 처럼 `!`를 붙인다 → 다이제스트에 💥 표시.

| type | 다이제스트 카테고리 |
|---|---|
| `feat` | ✨ 새 기능 |
| `fix` | 🐛 버그 수정 |
| `perf` | ⚡ 성능 개선 |
| `refactor` | ♻️ 구조 개선 |
| `docs` | 📝 문서 |
| `test` | ✅ 테스트 |
| `build`·`ci`·`chore`·`style` | 🔧 빌드/기타 |

**선택 트레일러** — 제목만으로 요약이 부족하거나 영어 병기가 필요하면 PR 본문에 넣는다.

```
릴리즈노트: 회의록 페이지에서 깨진 캘린더 링크 제거
릴리즈노트(en): remove broken calendar links from the meeting-notes page
```

- `릴리즈노트:`가 있으면 다이제스트는 제목 요약 대신 이 한 줄을 쓴다.
- `릴리즈노트(en):`는 `/members/releases`에 영어 보조 줄로 표시된다.

**다이제스트에서 빠지는 것** — dependabot 등 봇 PR, `(auto)`로 끝나는 자동 생성 커밋
(예: `content: daily research 2026-07-21 (auto)`)은 노이즈라 자동 제외된다. 실제로
사람이 배포한 변경만 남는다.

---

## PR 작성자 기준

PR 작성자는 다음을 기억한다.

1. PR 생성 Skill의 inspect/assessment/prepare를 거친다. plan만으로 생성 검토를 대체하지 않는다.
2. high-risk 변경이면 `--auto-merge`를 붙이지 않는다.
3. low-risk 반복 작업이면 `--auto-merge`를 붙일 수 있다.
4. reviewer를 요청하지 않는다. 사용자가 해당 PR의 peer review를 명시한 경우에만
   `--request-reviewers`를 사용한다.
5. merge는 required checks, exact-head 상태, 적용 가능한 증거 게이트와 실제 branch
   protection이 결정한다.
6. 제목은 `type(scope): 한글 요약` 규약을 지킨다(위 "릴리즈 노트 규약"). 이게
   매일 나가는 배포 다이제스트의 한 줄이 된다.

`hype-review`는 리뷰어가 자기 lens로 질문하게 만드는 도구이고, `hype-pr`는 작성자가
PR을 만들 때 팀 운영 정책을 자동으로 적용하게 만드는 도구다.
