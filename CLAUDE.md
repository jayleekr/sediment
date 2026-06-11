# CLAUDE.md — Sediment

> Per-project instructions. Loaded automatically when the working tree is
> inside `products/sediment/`. Augments the root `CLAUDE.md` (which covers
> the Deck pipeline + content pipeline). Do NOT duplicate content here that's
> already in the root file.

## Brand

**Sediment** — "where doing becomes knowing".

Fully renamed from the previous "AI Curator" codename on 2026-05-15; a
final remnant purge landed 2026-06-11. The following internal identifiers
still retain `curator`/`ai-curator` deliberately (not user-visible,
breaking-change cost > value):

- Rubric phase IDs (P0–P3) and check IDs (`P2-WEB-*`, etc.) — stable contract
- `curator.token` / `curator.token_rejected` web-storage keys — invalidating
  active dev tokens has no upside
- Local dev DB cluster identity: container names (`curator-pg`,
  `curator-redis`), DB/user `curator`, roles `curator_app`/`curator_service`
  (incl. `infra/init.sql` and the CI Postgres service that loads it) —
  renaming would invalidate every dev's local volumes
- JWT `ai-curator-local` / `ai-curator-services` issuer/audience defaults —
  changing them revokes every outstanding local token
- Vault ingest paths / search synonyms pointing at `products/ai-curator` —
  historical content in the lab vault still lives under that name
- `NEXT_PUBLIC_CURATOR_*` env fallback in `frontend/app/auth.ts` +
  `lib/api.ts` — Vercel prod still sets the old names; drop the fallback
  after adding `NEXT_PUBLIC_SEDIMENT_*` in Vercel
- `applications/curator_guardrails/` — placeholder for a planned (not yet built) service

Everywhere else — directories, modules, env vars, URLs, MCP tools, agents,
skills, Fly app slug — is Sediment.

---

## What this project is

Sediment is HypeProof Lab's evidence-grounded **memory layer** — every answer
comes with citations. Designed to scale into a multi-tenant SaaS. See
`SPEC.md` for full design and `DECISIONS.md` for the 20+ resolved
product/commercial decisions.

Claude-specific note: `AGENTS.md` is the canonical cross-agent operating
contract. Follow it first for PR close-out, dirty worktree discipline, branch
protection, and production validation evidence. This file adds Claude-specific
harness and project-memory details only.

---

## Read-before-edit

| File | Purpose | Don't edit unless |
|---|---|---|
| `infra/init.sql` | RLS schema DDL | catastrophic legal exposure (guard.json blocks) |
| `.env` | API keys | (guard.json blocks) |
| `services/sediment/applications/sediment_platform/routers/billing.py` | Stripe webhook | (guard.json blocks) |
| `services/sediment/validator/rubric.yaml` | validator contract | parent session only, document the why |
| `services/sediment/validator/recipes.yaml` | 4-tier code-mod policy | adding a new pattern is fine |
| `services/sediment/validator/e2e_spec.yaml` | E2E flow contract | parent session only |
| `harness/ralph/RALPH_PROMPT.md` | Ralph agent contract | careful — change behavior |

`.claude/guard.json` (at repo root) blocks edits to the catastrophic-blast-radius
files at the tool level — Edit/Write tools fail if a path matches.

---

## The harness — what each helper does

### Validator (the source of truth)

```bash
make validate-p0   # Phase 0: scaffolding + RLS
make validate-p1   # Phase 1: read-only index
make validate-p2   # Phase 2: chat MVP (SSE + intent + RLS + E2E)
make validate-p3   # Phase 3: ingest automation (cron + Discord)
make validate-all  # all phases sequential
```

Output → `output/validation/<phase>-iter*.json`. Score thresholds in `rubric.yaml`.

### AI commit protocol

```bash
bash harness/scripts/ai-commit.sh baseline <CHECK_ID> <PHASE>
bash harness/scripts/ai-commit.sh begin    <CHECK_ID>
# (write code via Edit tool)
bash harness/scripts/ai-commit.sh gate     <CHECK_ID> <PHASE>
# (gate auto-bounces services + runs lint-sql before validator)
bash harness/scripts/ai-commit.sh commit   <CHECK_ID> [MSG]
bash harness/scripts/ai-commit.sh rollback <CHECK_ID> [SHA]
```

### Service bounce on code change

```bash
make bounce-services BASELINE_REF=HEAD~1
# OR
bash harness/scripts/restart-services-if-changed.sh HEAD~1
```

Maps changed paths under `services/sediment/applications/<svc>/` and
`lab_lib/`/`lab_platform/` to running uvicorn pids; kills + restarts only
those. Idempotent (no changes → no-op). Wired into `ai-commit.sh gate`.

### SQL `:NAME::TYPE` lint

```bash
make lint-sql
# OR scan specific files
bash harness/scripts/lint-sql-cast.sh path/to/file.py
```

Forbids `:NAME::TYPE` (SQLAlchemy / asyncpg incompatibility — see LEARNINGS
2026-05-05 #test-04 + 2026-05-08 ai_coder_dispatch_real). Wired into
`ai-commit.sh gate` as a hard block.

### Ralph supervisor (the autonomous loop)

```bash
# Background 50-iter run with $20 cost cap
bash harness/ralph/supervisor.sh --max-iter 50 --cost-budget 20 --max-restarts 2

# Resume after a stop
make ralph-resume

# Reset state (wipes TODO/JOURNAL/STATE, keeps templates)
make ralph-reset
```

Ralph wraps `claude -p` with retry, cost capture, state-file integrity guard,
and per-iter snapshot/restore. Env scrub baked in (caller doesn't need
`env -u CLAUDE_CODE_*`).

After supervisor exit:
```bash
bash harness/scripts/harvest-ralph-results.sh /tmp/ralph-50iter.log
# writes RALPH_50ITER_RESULT.md
```

### P3 daily cron (regression alerts)

```bash
make p3-cron-install   # idempotent: load com.hypeproof.sediment.p3-validator
make p3-cron-status    # is it loaded? + last 5 history entries
make p3-cron-uninstall
```

Daily 09:15. Runs `validator --phase P3`, compares to prior result, posts to
Discord (via existing `cron-prompts/notify-discord.sh`) on failure /
regression / lost blocker. Weekly Monday heartbeat on routine green days.

---

## Code modification policy (4-tier)

Per `services/sediment/validator/recipes.yaml`:

| Tier | Pattern | Permission |
|---|---|---|
| 1 ai_apply_immediately | `P*-INFRA-*`, `P*-HEALTH-*`, `P*-INGEST-01/02` | `sediment-fixer` direct (no review) |
| 2 ai_propose_review_commit | RAG, SEARCH, INGEST-04, E2E, SEC, INTENT, MCP, DDL (non-RLS), CHUNK, WEB (after adding) | `sediment-coder` + reviewer + commit |
| 3 human_required | `P*-RLS-*` | NEVER auto. Write work-order. |
| 4 forbid_ai_edit | init.sql, .env, billing.py, credentials* | guard.json blocks at tool level |

To add a pattern: edit `recipes.yaml` directly (parent session) and document
the why in the commit message.

---

## Session Close-Out Protocol

See `AGENTS.md`. Do not say a session can be closed until the completed work
is in a focused PR with validation evidence and linked issue updates. Do not
direct-push to `main`.

---

## Subagent dispatch pattern

Sediment-coder (model=opus) is dispatched via the Task tool with a
self-contained prompt that includes:
1. Pointer to `.claude/agents/sediment-coder.md` for the contract
2. The work-order as JSON inline
3. Pointer to LEARNINGS.md for prior patterns
4. Cost ceiling

Sediment-coder cannot itself dispatch the reviewer via the Task tool
(sub-sub-agent limit). It uses `claude -p --dangerously-skip-permissions
--model sonnet` headless instead — that runs at level 0 and bypasses the
limit. This is per the root CLAUDE.md "Architecture Principle: Shell Scripts
as Orchestrators".

When in doubt: parent session does direct edits; AI dispatch is for verifying
the workflow itself works, plus high-volume bulk fixes.

---

## Common gotchas (resolved, but pattern-aware)

| Gotcha | Fix in this codebase |
|---|---|
| `:NAME::TYPE` SQL cast | use `CAST(:NAME AS TYPE)`. Lint blocks via `ai-commit.sh gate`. |
| `SET LOCAL X = $1` | use `SELECT set_config('X', :v, true)` |
| Zero-vector embedding (no OpenAI key) → 0 search results | `node_library_search` detects + falls back to BM25-only with OR-joined ts_query |
| FastMCP introspection | `await mcp.list_tools()` (not `_tools` dict) |
| LLM provider auto-pick `claude_cli` for SaaS | `resolve_provider` requires explicit `LLM_PROVIDER=claude_cli`; default = offline |
| Persist after `[DONE]` | client closes connection, server cancels generator. Persist BEFORE [DONE]. |
| Cookie-consent modal blocks E2E clicks | `e2e_runner.py` pre-seeds `localStorage.cookie_consent` via `add_init_script` |
| Parent Claude env leaks to child claude -p | supervisor.sh + ralph.sh `unset` first action |
| In-coder gate under-measures delta | gate auto-bounces uvicorn services first |
| `fly ssh console -C` hangs non-interactively | use `bash harness/scripts/fly-exec.sh "<cmd>"` or `make prod-run` — wraps `fly machine exec` (sediment#54) |

---

## When to extend recipes.yaml

If a new phase or new check pattern starts failing in a way that has bounded
scope and clear semantics:

1. Confirm the pattern doesn't touch RLS, init.sql, .env, billing.py
2. Add the glob (e.g. `P*-NEWTHING-*`) to `ai_propose_review_commit` in `recipes.yaml`
3. Commit with explicit reasoning in the commit message
4. Dispatch sediment-coder with the work-order

Adding a pattern unlocks autonomous fix-by-sediment-coder for that whole
class of failure forever after.

---

## Phase 5.5 dogfood gate

10 measurable criteria gated by `feature_flags.dogfood_gate_active`. The
criteria themselves were maintained in an internal-only spec; the gating
mechanism (flag + measurement endpoints) is part of the public surface.

---

*Last updated: 2026-05-25*
