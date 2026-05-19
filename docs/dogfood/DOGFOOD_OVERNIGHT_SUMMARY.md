# Overnight summary — 2026-05-19 → review this first

> Built per "내가 내일 확인할거야 / 모든걸 다 자율진행해". Autonomous
> self-refining loop, **converged 8→9/9**, no interaction needed. Read order:
> this → `internal-loop.md` (runbook) → `loop-state.json` (trail).

## What this session was (and was NOT)

**Scope held firm to your splits:**
- ✅ Built/hardened: the **internal dogfood loop machinery** only, code under
  `~/CodeWorkspace/sediment/services/sediment/` + `docs/dogfood/`.
- ⛔ Did NOT touch (your explicit splits): **Restruct** (4-repo topology /
  harness / hypeprooflab cleanup = separate session), **gated ops**
  (Fly/Vercel/OAuth/`make seed`-vs-Fly = your track), **Studio** (no access),
  **pgvector / egress / public** (parked).

## The one idea

`scripts/distill.py` — the **"정리" agent**. It closes the 2 *structural*
breaks that made "왜 이렇게 결정했나" unanswerable:
1. consolidate only read `conversations` → Discord #weekly notes never
   distilled. distill adds the `events` source (drops #잡담).
2. `decisions` table was invisible to RAG (retriever = chunks⨝artifacts only)
   → distill lands each decision as a **citable `type=decision` artifact**
   (idempotent `ref=decision/<slug>`) + links `decisions.source_artifact_id`.

This is simultaneously the **GATE-A lever** and the **data-refinement moat
prototype** (the "refine a company's messy data → queryable asset" thing).

## Loop result

| iter | critic | action |
|---|---|---|
| 1 | **8/9** | 3 BLOCKING (silent artifact-drop / fake-offline dry-run / NULL conv_id dup) + 2 POLISH fixed; added `tests/test_distill.py` |
| 2 | **9/9** | critic re-verified all BLOCKING genuinely closed + hand-traced every test (pass) → *"decision-ready for the 5/28 session"*; cleared 3 final POLISH |

Commits pushed to `origin/main` (shared base): `0504ee9` (distill) →
`8e3e70c` (iter-1 fixes) → `d400259` (iter-2 polish, converged).

## What YOU must do (unchanged — still just task #5 + the morning queue)

Nothing new from the loop. Outstanding gated items remain **yours**:
1. **🔴 `make seed` vs Fly DB** → fixes the live `oauth-exchange` 500
   (`github_login` column). One run. (fly proxy → DATABASE_URL → seed_lab.)
2. GitHub OAuth App → `frontend/.env.local` AUTH_GITHUB_*.
3. Vercel project (root `frontend/`).
4. Install `infra/github-actions/vault-ingest.yml` into hypeprooflab + secrets.
5. The Restruct session (repo cleanup) — assign separately as planned.

## 5/28 dedicated session is turnkey

`docs/dogfood/internal-loop.md` is the cold-start runbook. Sanity offline now:
`cd services/sediment && python -m pytest tests/test_distill.py` (works from
any dir now — POLISH-B added `pythonpath`), and
`python -m scripts.distill --dry-run` (genuinely DB/LLM-free).
Acceptance on 5/28: ask the live API *"왜 sediment를 분리했지?"* → it cites a
`decision/...` artifact (previously impossible).

A scheduled fallback wakeup may still fire — it reads `loop-state.json`
(`status=done`) and no-ops. Nothing to stop.
