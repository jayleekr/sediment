# START HERE — Overnight Run (Zero Prompts)

> Jay 자기 전 paste 한 번. 모든 script는 `bash <path>`로 호출되므로 `chmod`
> 권한 prompt가 발생하지 않는다. 새벽에 일어나면 `/curator:status`로 확인.

## 자기 전 1회 (Terminal에 paste)

```bash
# 0. (한 번만) Docker Desktop 시작 — GUI라 자동화 불가
open -a Docker
sleep 20

# 1. .env 준비 — 이미 있으면 skip
cd /Users/jaylee/CodeWorkspace/hypeproof/.claude/worktrees/mvp
test -f products/sediment/.env || cp products/sediment/.env.example products/sediment/.env

# ⚠ 이 단계만 사람이 — .env 열어서 ANTHROPIC_API_KEY + OPENAI_API_KEY 채워넣기
# 키 없으면 offline mode로 동작 (RAG 품질 0이지만 구조는 검증됨)

# 2. Bootstrap + Ralph supervisor (background)
nohup bash products/sediment/harness/bootstrap-all.sh --with-ralph \
  > /tmp/curator-overnight.log 2>&1 &
echo $! > /tmp/curator-overnight.pid
echo "started overnight run, pid=$(cat /tmp/curator-overnight.pid)"

# 3. (선택) HTML 대시보드 백그라운드
nohup bash products/sediment/harness/monitor/dashboard-loop.sh \
  > /tmp/curator-dashboard.log 2>&1 &

# 4. 잠자기. 휴.
```

## 새벽에 일어나서 (Claude Code 세션에서 1줄)

```
/curator:status
```

또는 자세히:
```
/curator:restart       # 죽었다면 재시작
/curator:medic         # 멈췄다면 진단
/curator:learnings     # 무엇을 배웠는지
```

## 무엇을 자동으로 처리하는가

| 야간 발생 사건 | 처리자 | 회복 시간 |
|---|---|---|
| Anthropic 429 rate-limit | ralph.sh exp backoff (5회) | 30~150s |
| 네트워크 timeout | ralph.sh retry | 30s |
| 서비스 죽음 (ingester crash) | curator-medic → curator-fixer | 1-2 iter |
| 정체 task (5 iter 무진전) | curator-medic + LEARNINGS append | 1 iter |
| 점수 회귀 | curator-medic → 매칭 specialist | 1-2 iter |
| API 예산 초과 | ralph 정상 종료 (`cost_budget_exhausted`) | terminal |
| Ralph 자체 죽음 | supervisor.sh 재시작 (cooldown 60-300s) | 60-300s |
| 30분 내 5회 crash | supervisor가 CRASH_REPORT.md 쓰고 종료 | terminal |

## 권한 prompt 안 뜨게 만든 방법

1. **모든 script는 `bash <path>`로 호출** → execute bit 불필요 → chmod 불필요
2. **Ralph subprocess는 `--dangerously-skip-permissions`** → 내부에서 모든 명령 자유
3. **outer wrapper는 `make` / `bash` / `nohup`만 사용** — 이미 global allowlist에 있음

## 만약 새벽에 봤는데 prompt가 떠 있다면

- 진행이 멈춰 있었다는 신호
- 그냥 `Esc`로 dismiss 후 `/curator:restart`
- supervisor가 알아서 재시작 시도

## 비용 예상

- Sonnet 4.6: $3/Mtok in / $15/Mtok out
- iter당 평균 ~$0.30~0.50 (compaction 적용 시 더 낮음)
- 200 iter 한도 × $0.50 = **최대 $100/run** (하드 캡 = `cost_budget_usd`)
- 더 빡빡하게: `--cost-budget 30` 전달

## 결과물 위치

| 파일 | 용도 |
|---|---|
| `products/sediment/harness/ralph/STATE.json` | 현재 상태 (iter, phase, cost) |
| `products/sediment/harness/ralph/JOURNAL.md` | 실행 로그 (append-only) |
| `products/sediment/harness/ralph/LEARNINGS.md` | 누적된 교훈 (append-only) |
| `products/sediment/harness/ralph/TODO.md` | 진행 체크리스트 |
| `output/validation/loop-PN-*/convergence.md` | phase별 수렴 결과 |
| `output/validation/loop-PN-*/iter-NN/work-order.json` | 사람 검토 필요한 항목 |
| `output/ralph/CRASH_REPORT.md` | (있다면) 5회 crash 후 항복 보고서 |
| `output/ralph-dashboard.html` | 브라우저용 라이브 대시보드 |

## 현재까지 만들어진 것 (시스템 인벤토리)

- 7 subagents (`.claude/agents/curator-*.md`) — SSL manifest
- 13 slash skills (`.claude/skills/curator/*` + `/curator-validate`)
- Validator harness (Python, 22 files, ~2500 LOC)
- Ralph loop + supervisor (rate-limit aware, auto-restart)
- 80+ rubric checks across 4 phases (P0~P3)
- 8 Playwright E2E flows with screenshot
- 40 golden RAG queries
- Permission patch + bootstrap + monitor + dashboard
