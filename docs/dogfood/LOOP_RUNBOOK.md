# Overnight autonomous hardening loop — internal dogfood machinery

Started 2026-05-19 night. User: "내가 내일 확인할거야 / 모든걸 다 자율진행해"
— full autonomy, no interaction, Jay reviews tomorrow.

## Goal

Harden the **internal dogfood loop machinery** (this session's scope only) so
the 5/28 dedicated session is turnkey and the code is correct/honest/robust.
Deliverables under review: `scripts/distill.py`, `scripts/dogfood_digest.py`,
`validator/checks/p5_activation.py`, the §9 sensors (cite_export router,
query-event, vault freshness), `infra/SUPABASE_MIGRATION.md`,
`docs/dogfood/*`.

## Algorithm

1. `critic` sub-agent (opus, adversarial, read-only) reviews the deliverables
   against the rubric below → structured findings + score.
2. Apply BLOCKING + high-value fixes (incl. adding offline unit tests for the
   pure logic — testable with NO DB/LLM).
3. Commit + push to `origin/main` (shared base; Jay reviews tomorrow).
4. Re-critique. Repeat.

## Rubric (6 axes, score = round(mean×2), ceiling 9)

- Correctness — logic/SQL/path bugs; would it actually run on the 5/28 session?
- Honesty — no fabricated metrics/decisions; degrades with flags, never fakes a pass
- Reuse discipline — wraps existing pieces, no reinvention/duplication
- Scope discipline — internal-only; does NOT drift into Restruct / gated / Studio / pgvector
- Robustness — offline/no-DB/no-LLM degradation; idempotency; failure isolation
- Turnkey readiness — could the 5/28 session run it cold from the runbook and pass?

## Termination

score ≥ 9 (1 iter) OR plateau (3 iters no gain) OR max 6 iters OR after
2026-05-20 07:30 KST. On any: status=done, idempotent (later wakeups no-op).

## Hard constraints (NON-NEGOTIABLE — baked from user's explicit splits)

- Edit ONLY under `~/CodeWorkspace/sediment/` (services/sediment, docs/dogfood,
  infra docs). Code = `services/sediment/` only.
- DO NOT do Restruct: no 4-repo topology, harness extraction, hypeprooflab
  cleanup, proxy-poc dedup, frontend-cutover deploy. (Separate session.)
- DO NOT do gated ops: no Fly deploy/secrets, Vercel, GitHub OAuth App,
  `make seed` vs Fly. (Jay track #5.)
- DO NOT touch Studio (no access) / pgvector-live / egress / anything public.
- No destructive ops. Commit+push to origin/main only (established shared
  base). No external sends. No new external services.
- Live runs stay gated (no venv/DB this environment) — verify via py_compile,
  AST, offline unit tests, and the critic. Never claim a live pass.

## Recovery

Read this → `loop-state.json` → continue from `next_action`. If status=done,
no-op. Loop driven by chained `critic` sub-agents + a long ScheduleWakeup
fallback heartbeat.
