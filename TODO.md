# Sediment — TODO (actionable backlog)

> **Generated 2026-06-04** on `worktree-todolist`. Companion to `NEXT.md`:
> NEXT.md is the narrative roadmap; this is the executable checklist. Each item
> is sized to be picked up cold and lists {근거 path / 완료조건 / 4-tier class}.
> 4-tier policy = `services/sediment/validator/recipes.yaml` (also CLAUDE.md).
> Hand this file to `/goal` via `GOAL_PROMPT.md` for autonomous execution.

## Current measured state (2026-06-04)

| Signal | Value | Source |
|---|---|---|
| Prod API healthz | **200** (~1.0s) | live `curl https://hypeproof-sediment.fly.dev/healthz` |
| Prod UI | **200** | live `curl https://sediment.hypeproof-ai.xyz/sediment` |
| Live recall@3 | **NOT MEASURED** | `recall_live` 403 on dev-token mint (see T5) |
| Last on-record recall@3 | 26 PASS / 5 PART / 9 MISS · avg 71.2% | NEXT.md P2 (2026-05-21, Supabase) |
| Ralph loop | idle / clean (templates only) | `harness/ralph/` |
| Local full validator | needs the docker stack (`make validate-*`) | — |

## Priority backlog

### T1 — [ops] Destroy stale Fly Postgres cluster `hypeproof-sediment-db`
- **Why:** Supabase live ~2주; 옛 Fly PG는 stopped이나 여전히 과금/혼란. "1주 안정" 조건 충족.
- **근거:** `NEXT.md` §P2 (destroy 명령 inline).
- **완료조건:** `fly machine destroy 7815619b606198`, `fly volume destroy vol_v3ggq0qe0gomklx4`,
  `fly apps destroy hypeproof-sediment-db` 성공; 이후 prod healthz 여전히 200.
- **Tier:** ops (operator — `fly` 파괴적 작업, AI 코드 편집 아님).

### T2 — [ops] Finalize hypeprooflab redirect + delete dead `/sediment` route
- **근거:** `NEXT.md` §P0 Remaining. **`hypeprooflab` repo 소관, 이 repo 아님.**
- **완료조건:** 307→308 flip 안정; hypeprooflab의 `web/src/app/sediment/` 삭제; 옛 URL은 새 도메인으로.
- **Tier:** cross-repo.

### T3 — [ux] Fly root `/` → Vercel UI 302 (kill the 404 dead-end)
- **✅ DONE (verified 2026-06-04)** — already shipped: `nginx.conf:195` does `location / { return 302 …sediment.hypeproof-ai.xyz }`. Prod `/` → 302→UI, `/healthz` 200. No change needed.
- **근거:** `infra/deploy/nginx.conf` (`location / { return 404; }`); `NEXT.md` §P4.
- **완료조건:** Fly `/` 브라우저 GET → UI 302; API 경로는 현행 유지; post-deploy E2E-12 green.
- **Tier:** 2 (ai_propose_review_commit — nginx, non-RLS).

### T4 — [data] Vault freshness metric ("updated N h ago")
- **✅ DONE (verified 2026-06-04)** — already shipped: `GET /api/v1/vault/freshness` (`vault.py:64`, tenant-scoped) + `FreshnessBadge` mounted in `frontend/app/sediment/layout.tsx:36` ("vault Nh ago"). Live on prod (401 auth-gated); 55 vault/freshness tests pass + ruff clean. No change needed.
- **근거:** `NEXT.md` §P1 freshness row; `services/sediment/config/cron.yaml` (github_repo_sync/consolidate).
- **완료조건:** `/healthz`(혹은 sibling)가 vault last-updated를 보고; UI 노출. 머지→반영이 보일 것.
- **Tier:** 2.

### T5 — [ops/sec] Restore the live-recall signal (dev-token 403)
- **Why:** `recall_live` + `nightly-recall.yml`이 `POST /api/v1/auth/dev-token`로 JWT를 mint하는데,
  prod는 보안상 403(SEDIMENT_DEV_MODE 미설정, CVE급 auth-bypass 수정). 매일 recall 신호가 죽음.
- **근거:** `services/sediment/applications/sediment_platform/routers/auth.py` (dev_token gate);
  `services/sediment/validator/scripts/recall_live.py`; `.github/workflows/nightly-recall.yml`.
- **완료조건:** nightly-recall이 prod에 green (CI-safe 인증경로: OAuth-device 또는 scoped CI 토큰),
  dev-token bypass 재활성화 없이. recall_live가 CI에서 exit 0/2(1 아님).
- **Tier:** 2 (SEC/CI — auth 보안 태세는 건드리지 말 것).

### T6 — [rag] Refresh stale `ideal_refs` (#10)
- **Why:** vault 563→690 성장으로 ~14개 golden query의 ideal_refs가 없는 파일 참조 → 데이터 사유로 저점.
- **근거:** `services/sediment/validator/golden_queries.yaml`; `NEXT.md` §P2 (#10). (T5 복구 후 재측정.)
- **완료조건:** dangling refs 갱신; live recall PASS ≥ 26/40.
- **Tier:** 2 (RAG).

### T7 — [ops/sec] P5 loose ends
- **근거:** `NEXT.md` §P5; `services/sediment/data/members.json`.
- **완료조건:**
  - [ ] Simon email을 `data/members.json`에 추가 + reseed.
  - [ ] HypeProof 자체 Anthropic 키 provision + `fly secrets set` (현재 임시 차용 키).
  - [ ] gemini-2.5-pro는 flash 유지, 보류.
- **Tier:** mixed (reseed=Tier2, secrets=operator).

### T8 — [data] L3 Discord knowledge ingest — **Jay go-ahead 필요**
- **근거:** `NEXT.md` §P1 L3. (Discord chat ingest는 이미 scheduler로 가동 중; 이건 큐레이션 레이어.)
- **Tier:** 2 — **Jay의 명시적 승인 전 시작 금지.**

### T9 — [gate] Phase 5.5 dogfood gate week-1 measurement
- **근거:** 10 criteria in standalone `validator/checks/p5_dogfood.py` (no feature flag). Adoption clause (5/6/7/9) superseded by DECISIONS.md 2026-05-19 ship-gate (S3/S4). `PHASE_5_5_DOGFOOD_GATE.md` is referenced but absent.
- **완료조건:** week-1 측정 착수, 10개 항목 각각 현재값 또는 측정계획.
- **Tier:** gate (human 결정; 계측=Tier2).

## Do-not-touch (guardrails)
- `.claude/guard.json` 차단: `infra/init.sql`, `.env`, `billing.py`, `credentials*` (Tier 4).
- `P*-RLS-*` = Tier 3 human_required, 자동 금지.
- `make reset`, `docker compose down -v`, `force-push` 금지.
