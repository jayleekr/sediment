# 요구사항에서 다음 작업 찾기

> 작성일 2026-09-13 · 상태: 활성 · Harness #161

## Intent

승인된 제품 의도가 아직 충족되지 않았다면 에이전트가 다음 설계·구현·검증 작업을
찾을 수 있어야 한다. 한 계획의 완료, 열린 PR 0개, change-impact 처리 완료는 제품의
미충족 요구사항이 없다는 뜻이 아니다.

## 요구사항

- WD-01: 원문 파일과 그 안의 모든 요구사항 ID를 조사한다. 누락·내용 변경·새 문서는
  작업 분해 대상으로 보고한다. 읽기 실패를 빈 목록으로 바꾸지 않는다.
- WD-02: 각 요구사항은 Intent, 디자인, 테스트 계획, 도메인 owner와 실행 packet에
  연결한다. packet은 기존 이슈, 다음 행동, 변경 설계, 양성·음성 대조군, 증거를 지정한다.
- WD-03: 설계/구현/검증/연구와 ready/claimed/in_review/dependency/blocked/reconcile/complete를
  분리한다. 사람 인수 대기가 독립적인 계측·fixture·로컬 구현까지 막지 않도록 packet을 나눈다.
- WD-04: 닫힌 이슈와 병합된 PR만으로 충족 처리하지 않는다. 범위별 검토자·보고서·
  요구사항/구현/검사 입력 hash가 있는 완료 기록을 확인한다. 입력 변경은 재검증으로 돌아간다.
  완료는 검토한 packet 범위(요구사항 ID·검증 입력·양성/음성 대조군·증거 기준)에 묶인다.
  같은 파일이라도 packet에 요구사항이 추가되거나 인수 기준이 바뀌면 재검증으로 돌아간다.
- WD-05: 전체 GitHub 이슈·PR을 현재 조회하며 fresh wip를 보존한다. 조회 실패·오래된
  snapshot·claim 시각 불명은 가용성 미확인이다. 목록은 추천이며 claim과 merge를 수행하지 않는다.
  열린 PR의 자동 닫기 링크(closing)와 관계 키워드가 붙은 명시 참조(related: `Refs #N`, `Part of`,
  `See`, `Implements` 등 뒤의 `#N`·같은 저장소 `owner/repo#N`·URL)는 진행 중인 작업으로 보고 해당
  packet을 in_review로 둔다. 키워드 없는 단순 언급(mentions: "#123 과 달리", "#456 후속")은 작업으로
  세지 않고 `mentioned_in_prs`와 사유에만 표시한다. 다른 저장소 참조는 포함하지 않는다.
  claim 댓글은 원장이 추적하는 wip 이슈에만 조회한다.
- WD-06: "할 일 없음"은 등록 범위, 조회 시각, 미등록/미충족/검증 대기/사람 대기 수와
  함께 보고한다. ready 0개는 종료의 증거가 아니다. 미등록 영역까지 제품 완료를 확장하지 않는다.

## 설계

정본 실행기는 `scripts/work-discovery/discover.py`, 제품별 원장은
`config/requirement-work.json`이다. 원장은 요구사항을 복제하지 않고 경로·ID·hash를
보존한다. 제품 manifest의 `requirement_globs`와 제외 사유도 검토 대상이다.
ID는 문서별 범위이며 현재 파서는 Markdown 표 첫 열과 `### VO-01` 형식의 정의를 읽는다.
NAT처럼 표 안 문장에 정의한 ID는 문서별 `inline_prefixes`를 명시한다. 다른 형식은
명시적인 parser 확장이 필요하며 비어 있는 문서를 통과시키지 않는다.

```bash
python3 /path/to/hypeproof-harness/scripts/work-discovery/discover.py --checkout .
python3 /path/to/hypeproof-harness/scripts/work-discovery/discover.py --checkout . --check
```

첫 명령은 GitHub read-only 조회 후 추천한다. 두 번째는 오프라인 구조 검사만 한다.
`--save-snapshot /tmp/work.json` / `--snapshot /tmp/work.json`으로 1시간 이내 조회를
재사용할 수 있다. snapshot은 PR 본문·자격을 포함하지 않고 제품 repo에 커밋하지 않는다.

완료 attestation은 에이전트 판단이다. `completion`의 `reviewed_by`, `report`, `inputs`,
`verdict: PASS`를 검토하며 `verification_inputs`에 구현·테스트·fixture를 지정한다.
`completion.scope_sha256`에는 검토 시점의 packet 범위 digest를 기록한다
(`discover.py --checkout . --scope-digest <packet id>`). 값이 없거나 현재 범위와 다르면 완료가 아니다.
파일 존재/hash 일치는 그 증거의 독립 실행·인간 승인·실제 학습 효과를 증명하지 않는다.
설계 문서나 테스트 파일의 존재도 의미적 충족의 보증이 아니다.

## 세션 절차

1. 최신 main과 현재 작업 branch를 비교하고 활성 작업/PR을 조회한다.
2. 제품 원장을 검사한다. 없으면 그것이 첫 분해 작업이다. 일부 DAG만 읽고 종료하지 않는다.
3. ready packet의 원문과 현재 구현을 읽고 기존 기능을 재사용한다. 큰 packet은 첫 PR
   하나로 잘라 하위 이슈·허용 경로·의존성·검증을 적는다. ready는 전체 인수 가능 선언이 아니다.
4. GitHub를 다시 확인하고 이슈별 wip/담당/세션/branch를 남긴다. 공통 App·포트는 별도 예약한다.
5. 수정 후 검증과 PR 준비를 수행한다. 완료한 범위만 기록하며 남은 조건은 새 packet으로 연결한다.
6. ready가 없으면 claimed/in_review/dependency/blocked/reconcile과 gap을 보고한다.
   사람 결정·실기 환경·운영 승인 각각의 구체적인 해제 조건을 적는다.

기존 DAG의 순서·human gate는 그 계획 내부에서 유지한다. 별개의 기능을 옛 계획의
human gate 뒤에 임의로 넣지 않으며, 새 원장이 배포 승인이나 비용 권한을 만들지 않는다.

## 검증

`python -m pytest tests/work_discovery -q`.
정상 원장, 누락/추가/수정 REQ, 미등록 문서, 순환 의존성, 닫힌 이슈, fresh/stale claim,
입력 변경 후 완료 무효화, packet 범위 확장·인수 기준 변경·범위 digest 누락 시 완료 무효화,
사람 gate, 열린 PR의 자동 닫기·명시 참조·단순 언급 구분(다른 저장소 제외), 원문 접근 실패를 대조한다.
이 테스트는 작업 탐색 계약의 검사이며 Studio의 제품 인수가 아니다.
