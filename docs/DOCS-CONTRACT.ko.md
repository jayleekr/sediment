# HypeProof Dev Docs Contract

이 문서는 Studio, Sediment, Lab 계열 repo가 공통으로 따라야 하는 개발 문서
계약이다. 원천 문서는 각 제품 repo에 둔다. `hypeprooflab`은 멤버용 포털,
인증, 렌더링, 배포, 시각 검증을 담당하고 제품 지식의 canonical source가
되지 않는다.

## Ownership Model

| Layer | 책임 | 위치 |
|---|---|---|
| Source repo | 제품 의도, 아키텍처, 요구사항, 테스트, 운영, 릴리즈, UX evidence | `hypeproof-studio`, `sediment` |
| Shared harness | 문서 구조, frontmatter schema, drift/품질 검사 | `hypeproof-harness/scripts/docs-harness` |
| Member portal | 선별, 인증, 렌더링, Vercel 배포, screenshot/E2E gate | `hypeprooflab` |

## Required Files

각 제품 repo는 아래 파일을 가져야 한다.

```text
hypeproof.docs.yaml
docs/dev/00-overview.md
docs/dev/01-architecture.md
docs/dev/02-directory-structure.md
docs/dev/03-runtime-flows.md
docs/dev/04-requirements.md
docs/dev/05-testing-requirements.md
docs/dev/06-release-process.md
docs/dev/07-operations.md
docs/dev/08-ux-evidence.md
docs/adr/README.md
docs/adr/0001-*.md
```

## Frontmatter Schema

모든 `docs/dev/*.md` 문서는 같은 frontmatter를 사용한다.

```yaml
---
title: Architecture
product: studio
doc_type: architecture
status: canonical
owner: core
version: 0.1.4
last_reviewed: 2026-05-22
audience: maintainers
source_paths:
  - extensions/hypeproof-chat/src
quality_gates:
  - diagrams-render
  - links-valid
---
```

`version`은 각 repo의 source of truth와 일치해야 한다.

- Studio: `extensions/hypeproof-chat/package.json`
- Sediment: `services/sediment/pyproject.toml`

## Content Standard

| 문서 | 반드시 포함할 내용 |
|---|---|
| `00-overview.md` | 제품 목적, 핵심 사용자, repo가 책임지는 범위, non-goals |
| `01-architecture.md` | C4 Context/Container/Component 관점, Mermaid 또는 PlantUML diagram |
| `02-directory-structure.md` | 실제 디렉토리 tree, ownership boundary, 수정 금지/주의 구역 |
| `03-runtime-flows.md` | 주요 request/event/user flow, 실패 경로, observability signal |
| `04-requirements.md` | `REQ-*` ID, 수용 기준, 관련 source/test path |
| `05-testing-requirements.md` | unit/e2e/manual 계층, 실행 가능한 test command, release gate |
| `06-release-process.md` | version bump, changelog/release note, deploy, rollback |
| `07-operations.md` | local setup, secrets, common failure, incident response |
| `08-ux-evidence.md` | screenshot/GIF 위치, 캡처 방법, 검증 시나리오 |

## Gate

각 repo에서 다음 명령이 95점 이상으로 통과해야 한다.

```bash
python3 scripts/docs-harness/check.py --min-score 95
```

하네스는 외부 패키지 없이 실행된다. CI에는 이 명령을 그대로 붙인다.

## Authoring Rule

기능 PR이 다음 중 하나를 바꾸면 같은 PR에서 관련 문서를 갱신한다.

- public behavior 또는 UX
- auth, persistence, storage, deployment, observability
- test command 또는 release gate
- architecture boundary 또는 directory ownership
- version source, release artifact, rollback path

문서가 실제 코드와 맞지 않으면 문서가 아니라 PR이 실패해야 한다.
