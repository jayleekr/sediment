# Sediment CLI — 5분 시작 (팀원용)

> 이 문서를 읽으면서 따라하면 5분 안에 본인 노트북에서 Sediment 검색이
> 동작합니다. 엔지니어가 아니어도 OK.

---

## 1. 설치 (1분)

```bash
brew install hypeprooflab/tap/sediment
```

> 처음 tap 사용 시 자동으로 `brew tap` 됩니다. 깔린 게 맞는지 확인:
> ```bash
> sediment --version
> ```
> 버전이 보이면 OK.

---

## 2. 로그인 (1분)

```bash
sediment auth login
```

다음 두 줄이 터미널에 뜹니다:
```
To finish login, open this URL in any browser:
    https://sediment.hypeproof-ai.xyz/device?user_code=ABCD-EFGH

And enter the code (if prompted): ABCD-EFGH
```

브라우저가 자동으로 열리거나, 안 열리면 직접 위 URL을 복사해서 엽니다.
GitHub 로그인 → "Approve device" 버튼 → 끝.

터미널이 `{"ok":true, "account":"you@example.com", ...}` 로 변하면 성공.

---

## 3. 동작 확인 (30초)

```bash
sediment whoami
```

```
display_name  Jay
email         you@example.com
member_id     xxxxxxxx-...
role          creator
tenant_id     yyyyyyyy-...
```

본인 이름 + 테넌트가 보이면 끝.

---

## 4. 검색 한 번 해보기 (30초)

```bash
sediment search "research" --limit 3
```

세 개의 가장 관련성 높은 chunk가 표로 뜹니다. ref(파일 경로) +
score + 본문 일부.

자연어 질문도 가능:
```bash
sediment ask "what's our 0→1 fit?" --stream
```

LLM 답변이 한 글자씩 흘러나오고, 마지막에 citations(증거)가 붙습니다.

---

## 5. Claude Code 안에서 쓰기 (선택, 2분)

본인이 Claude Code 사용자라면, sediment CLI를 Claude Code 안에서
직접 호출할 수 있도록 연결:

```bash
pipx install sediment-mcp-shim
```

그리고 Claude Code 세션에서:
```
/sediment-connect
```

이후 어떤 Claude Code 세션에서도:
- "sediment search ..."
- "sediment ask ..."
- "sediment read ..."

같은 도구 호출이 가능해집니다.

---

## 자주 막히는 곳

| 증상 | 해결 |
|---|---|
| `brew install` 시 "tap not found" | `brew tap hypeprooflab/tap` 먼저 |
| `sediment auth login` 후 브라우저가 안 뜸 | 터미널에 찍힌 URL을 수동 복사해서 다른 브라우저에 |
| `auth_expired` 에러 | JWT는 24시간 만료 → `sediment auth login` 다시 |
| 한국어 검색이 안 됨 | 잘 됨 (`sediment search "한국어"` 가능). 따옴표 안에 넣어주세요 |
| 본인 계정 외 다른 사람으로 보고 싶음 | 권한 없음 — RLS로 강제 분리됩니다 (다른 테넌트 데이터는 보이지 않음) |

---

## 다음 단계

- 매일 검색 1-2번 해보기 (`sediment recent --days 7` 도 좋음)
- 잘 안되는 쿼리/이상한 결과 있으면 `#sediment` Discord 채널에 스크린샷
- 한 달 dogfood 후 피드백 모아서 v1.1 개선

---

*문서 작성: 2026-05-22*
*소속 design 문서: [docs/design/cli-multi-user-access.md](design/cli-multi-user-access.md)*
