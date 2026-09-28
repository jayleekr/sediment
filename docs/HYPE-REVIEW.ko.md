# hype-review

> 이 문서는 `hypeproof-harness:docs/HYPE-REVIEW.ko.md`가 원천이다.
> 제품 repo에 vendoring된 사본은 직접 고치지 말고 harness에서 PR로 바꾼다.

---

## 목적

`hype-review`는 승인을 자동화하는 도구가 아니다. 팀원이 자기에게 온 PR 리뷰
요청을 찾고, 맡은 역할의 관점으로 질문하고, 리뷰가 끝났을 때 작성자에게 남길
답변을 정리하게 만드는 학습용 워크시트다.

운영 원칙은 다음과 같다.

- 리뷰 요청은 가능한 모든 active 멤버에게 보낸다.
- merge는 모든 사람이 승인할 때까지 기다리는 방식이 아니라 branch protection,
  CODEOWNERS, required checks가 요구하는 quorum을 따른다.
- 보안, 배포, repo governance, 데이터 격리처럼 blast radius가 큰 변경은 quorum을
  만족해도 maintainer가 추가 리뷰를 요구할 수 있다.
- 리뷰 코멘트는 취향 표현이 아니라 작성자가 행동할 수 있는 질문, 근거, 기대
  수정으로 남긴다.

---

## 기본 사용법

Claude Code에서는 스킬을 먼저 쓴다.

```text
# 내게 요청된 open PR 목록 보기
/hype-review

# 특정 PR을 내 기본 lens로 리뷰하기
/hype-review https://github.com/jayleekr/sediment/pull/87
```

스킬은 현재 GitHub 사용자와 repo/PR을 추론한 뒤 내부적으로
`scripts/hype-review/review.py`를 실행한다. 평소에는 `--role`을 넣지 않는다.

CLI fallback은 다음과 같다.

harness repo 또는 vendored script가 있는 product repo에서 실행한다.

```bash
# 내게 요청된 open PR 목록 보기
python3 scripts/hype-review/review.py --mine

# 특정 PR을 역할 관점으로 리뷰하기
python3 scripts/hype-review/review.py \
  --repo jayleekr/sediment \
  --pr 87 \
  --reviewer TJ-kr

# GitHub 없이 워크시트 형식만 확인하기
python3 scripts/hype-review/review.py \
  --offline \
  --repo jayleekr/hypeproof-harness \
  --pr 24 \
  --title "Codify deploy governance and all-member review requests" \
  --reviewer JeHyeong2 \
  --risk governance
```

메인테이너가 PR을 만들었거나 Dependabot PR이 새로 생겼을 때는 reviewer 요청이
누락되지 않았는지 별도 audit을 먼저 돌린다. 기본은 read-only다.

```bash
# open PR 전체에서 누락된 reviewer 요청 보기
python3 scripts/hype-review/request_reviewers.py

# 특정 repo만 보기
python3 scripts/hype-review/request_reviewers.py --repo jayleekr/sediment

# 누락된 요청 실제 전송
python3 scripts/hype-review/request_reviewers.py --apply
```

이 스크립트는 `policy/members.yaml`의 active 멤버를 기준으로 author, 이미 요청된
사람, 이미 review를 남긴 사람을 제외하고 누락된 reviewer만 요청한다. 초대가
pending이라 GitHub가 reviewer로 인식하지 못하는 사용자는 dry-run에서
`pending_invitation`으로 남기고, 그 PR에는 멘션 코멘트로 fallback한다.
GitHub API가 pending invitation을 노출하지 못하는 repo에서는 `--apply` 결과가
`failed`로 남을 수 있다.

`--mine`은 로컬 `gh` 인증 사용자를 읽고 GitHub 검색으로
`review-requested:@me` PR을 찾는다. 팀원에게 `gh auth login`이 되어 있지 않으면
GitHub UI에서 PR URL을 복사한 뒤 `--repo`, `--pr`로 먼저 연습해도 된다.

사용자별 기본 lens는 `policy/members.yaml`의 `review_lenses`에서 읽는다.
따라서 평소에는 `--role`을 넣지 않는다. 특정 PR에서만 추가로 봐야 할 관점이
있으면 `--role security`, `--role docs`처럼 보강한다.

---

## 역할 lens

역할은 직책이 아니라 리뷰할 때 집중할 사고방식이다. 한 PR에 여러 역할을 붙여도
된다.

| Role | 언제 쓰나 |
|---|---|
| `contributor` | 일반 코드 변경, 설명/테스트/재현성 확인 |
| `maintainer` | repo 정책, 장기 유지보수, merge 책임 확인 |
| `product` | 사용자 가치, 표면 동작, 문서/문구 확인 |
| `frontend` | UI 상태, 접근성, 반응형, 화면 검증 확인 |
| `backend` | API 계약, 데이터, tenant, 에러 처리 확인 |
| `security` | 인증, 인가, 시크릿, 공개 repo 노출 확인 |
| `deploy` | CI/CD, preview, production, rollback 확인 |
| `docs` | 문서 원천, 링크, 포맷, 제품별 일관성 확인 |

기본 매핑은 다음과 같다.

| GitHub | Default lens |
|---|---|
| `jayleekr` | `maintainer`, `product`, `security`, `deploy`, `docs` |
| `JeHyeong2` | `maintainer`, `backend`, `security` |
| `ico1036` | `contributor`, `docs` |
| `xoqhdgh1002` | `contributor`, `product` |
| `JinyongShin` | `backend`, `deploy` |
| `TJ-kr` | `frontend`, `product` |
| `rabqatab` | `contributor`, `product` |
| `J3llyBe4n` | `contributor`, `backend`, `deploy` |

> 이 표는 사본이다. **정본은 `policy/members.yaml` 의 `review_lenses`** 이고 스킬이 읽는
> 것도 그쪽이다. 위 두 줄은 그 사본이 갈라져 있어서(멤버 둘이 빠져 있었다) 채워 넣은
> 것이다 — 멤버가 늘면 두 곳을 같이 고쳐라.

변경 파일 경로에서 보안, 배포, 데이터, UI, 문서, governance 위험이 감지되면 risk
lens 질문도 자동으로 붙는다. 누락된 위험은 `--risk security --risk deploy`처럼
강제로 추가한다.

---

## 리뷰 순서

1. PR 본문에서 닫는 이슈, 변경 이유, 테스트를 먼저 확인한다.
2. diff를 보기 전에 "이 변경이 실패하면 누가 피해를 받는가"를 적는다.
3. 공통 질문에 답이 안 나오면 작성자에게 질문한다.
4. 내 role lens와 감지된 risk lens만 깊게 본다.
5. 최종 판단을 `APPROVE`, `COMMENT`, `REQUEST_CHANGES` 중 하나로 고른다.
6. 하네스의 reply guide를 기반으로 작성자에게 답변한다.

---

## 답변 기준

### Approve

다음이 모두 맞으면 승인한다.

- 변경 의도와 실제 diff가 일치한다.
- 테스트 또는 화면 확인이 핵심 위험을 직접 증명한다.
- 남은 의견이 있어도 merge를 막는 blocker가 아니다.

답변은 짧게 남긴다.

```markdown
@작성자 확인했습니다. 변경 범위와 테스트가 PR 설명과 맞습니다.
제가 본 핵심 근거: <테스트/화면/파일>
남은 의견은 merge를 막지 않습니다: <있으면 한 줄>
```

### Comment

맥락 질문이나 개선 제안은 있지만 답변만으로 해소될 수 있으면 comment를 남긴다.

```markdown
@작성자 이 부분만 확인 부탁드립니다.
질문: <구체 질문>
왜 중요한지: <위험 또는 사용자 영향>
답변/근거가 확인되면 승인하겠습니다.
```

### Request Changes

보안, 데이터, 배포, 사용자 기능을 깨뜨릴 수 있는 blocker가 있으면 변경 요청을
남긴다. 이때는 반드시 파일/라인 또는 재현 경로를 붙인다.

```markdown
@작성자 이 PR은 merge 전에 수정이 필요합니다.
Blocker: <파일/라인 또는 동작>
Risk: <보안/데이터/배포/사용자 영향>
Expected fix: <원하는 수정>
Re-test: <작성자가 다시 돌려야 할 검증>
```

---

## 팀 운영 규칙

리뷰 요청을 받은 사람은 "내가 최종 결정권자라서 승인해야 한다"가 아니라 "내
역할의 lens로 팀이 놓칠 수 있는 질문을 하나라도 더 발견한다"는 태도로 본다.

반대로 PR 작성자는 모든 사람의 승인을 기다릴 필요가 없다. required checks와
branch protection이 통과했고, CODEOWNER 또는 maintainer quorum을 만족했으며,
남은 코멘트가 non-blocking이면 순서대로 merge한다. 단, 시크릿 노출, 권한 우회,
프로덕션 배포 실패 가능성이 언급된 코멘트는 명시적으로 해소한 뒤 merge한다.
