# 17 — Continuous Learning Roadmap (Long-Term Plan)

> Status: **plan** (authored 2026-07-06). Owner: Jay.
> Companion docs: `15-self-improving-rag.md` (system loop), `16-query-event-store.md`
> (signal store), `ICP-segmentation.md` (archetype D), `DECISIONS.md:88-105`
> (dogfood ship-gate).

## 0. Why this document exists

HypeProof Lab's go-to-market is education: lectures and courses funnel
attendees (ICP archetype **D** — post-consulting/lecture alumni, zero CAC,
highest price tolerance) into HypeProof Studio / Sediment as their ongoing
memory layer. That funnel only converts if Sediment's **continuous learning**
story is credible — attendees must feel the product keeps getting smarter
about *their* work, and keeps *them* learning after the course ends.

Repo audit (2026-07-06) found that "continuous learning" currently means two
different things, in two very different states:

| Loop | What it is | State |
|---|---|---|
| **Loop A — system learns** | Self-improving RAG flywheel: signal capture → LLM judge → golden-set growth → weekly tuning proposals (`15-self-improving-rag.md`) | **~80% built, starved.** Backend + 6 cron jobs shipped (`config/cron.yaml:105-150`), but the frontend never sends thumbs/copy/cite signals, live recall is unmeasured in prod, faithfulness is env-gated off, and every tuning output is a proposal nothing consumes. |
| **Loop B — learner learns** | Learner-facing features that make an alumnus keep coming back: resurfacing their own decisions, "what changed since your last visit," correction memory, progress/coverage views | **Absent.** Not in SPEC.md, not in code. Retention hooks today are a generic Discord digest + freshness badge. |

**The strategic bet: Loop B is the product for archetype D; Loop A is the
quality engine underneath it.** This roadmap sequences both, defines the
metrics gates between horizons, and specifies how Opus/Sonnet subagents
execute the work long-term.

---

## 1. North-star metric & PMF frame

North star: **weekly returning learners per cohort** — an attendee who, 4+
weeks after the course, still queries their Sediment tenant at least weekly.

PMF validation reuses the already-decided dogfood ship-gate
(`DECISIONS.md:88-105`, HARD LINE):

- **Ring 1** (own lab): activation `S3 ≥ 5/8`.
- **Ring 2**: `S3+` from ≥ 1 non-builder.
- No paying/commitment external tenant until both hold.

Honest PMF assessment as of today: archetype D's *willingness to pay*
(₩200–500K/mo tolerance) is asserted in ICP research but **untested**, and the
retention mechanism it depends on (Loop B) doesn't exist yet. Treat H2 below
as the PMF experiment, not a feature checklist: **2 lecture cohorts, explicit
kill/pivot criteria** (§6).

---

## 2. Horizon 0 — Close the starving loop (weeks 1–2)

Goal: every measurement that already exists starts producing real numbers.
No new product surface. All items are Tier-1/2 subagent-executable.

| # | Work item | Evidence / entry point |
|---|---|---|
| H0-1 | Wire frontend signal buttons: thumbs up/down, copy, cite-export, report-bad-answer → promote-to-golden. Endpoints already live (`routers/signals.py`, `feedback.py`, `promote_to_golden.py`, `cite_export.py`); no `.tsx` calls them. | `frontend/app/sediment/components/ui.tsx`, `lib/api.ts` (`citeExport()` defined, uncalled) |
| H0-2 | Restore daily live recall in prod (T5). `SEDIMENT_CI_TOKEN` path merged (#65) — verify it emits a daily number, persist per-run results for trend. | `TODO.md:9-18`, nightly recall workflow |
| H0-3 | Fix faithfulness runner: last run 404'd on wrong port (`:10101` vs langgraph `:10020`); enable `SEDIMENT_FAITHFULNESS_ENABLED` in scheduled validation so `P2-FAITH-*` gates are live. | `output/validation/p2-faithfulness-per-query.json`, `validator/checks/p2_faithfulness.py` |
| H0-4 | Recall/faithfulness **trend persistence**: append per-run scores to a durable log (repo or DB) so H1 tuning has a baseline curve, not single-shot pass/fail. | `validator/checks/lib_rag.py` |
| H0-5 | Resolve `dogfood_gate_active` fiction: the flag exists in CLAUDE.md, not in code. Either implement it around `p5_dogfood.py` or delete the reference. | `validator/checks/p5_dogfood.py` |

**Gate H0→H1:** daily recall + nightly faithfulness both emitting numbers for
7 consecutive days; ≥ 1 real FE-originated row in `message_signals`.

---

## 3. Horizon 1 — Retrieval depth to 90%+ (weeks 3–8)

Goal: break the recorded **BM25 recall ceiling of 77.5%**
(`harness/ralph/LEARNINGS.md`) and make quality claims defensible before
putting the product in front of cohorts. Levers ranked by expected impact:

1. **Re-ranker** (biggest missing lever — none exists today, only RRF k=60).
   Cross-encoder or LLM-rerank over the fused top-20, behind a flag, measured
   by A/B on the golden set via existing `ab_compare.py`.
2. **Make the vector arm actually fire.** `prefer_bm25_first()` skips
   embeddings for any Korean query or ≥4 signal tokens, so the semantic arm is
   frequently inert; zero-vector fallback adds fragility. Relax the bypass,
   guard with `is_zero_vector()`, measure per-category recall delta.
3. **KO↔EN vocabulary bridge.** Known failure class ("채점" vs "scoring").
   Options: query expansion at intent stage, or lean on Gemini multilingual
   embeddings once (2) lands. Replace `'simple'` tsvector config where it
   demonstrably loses.
4. **Golden set v1.0**: 40 → 120+ queries. Add precision@k, nDCG,
   per-category recall breakdown; fix hard/adversarial cases with empty
   `ideal_refs` (currently can't fail); LLM-assisted annotation with human
   spot-check to escape the single-annotator ceiling; pin refs against vault
   drift (recall once dropped to 25% purely from content moves).
5. **Consume `hard_negatives.jsonl`** (Monday cron writes it; nothing reads
   it). Minimum: feed into eval as regression cases. Later: boost-weight
   tuning input.
6. **De-brittle the hand-tuned BM25 boosts** (3x/2x/0.8x heuristics tuned to
   specific golden queries) — re-derive from ablation once the re-ranker
   absorbs precision duty.

**Gate H1→H2:** recall@3 ≥ 90%, MRR ≥ 0.70, faithfulness mean ≥ 0.80,
sustained over 14 days of nightly runs.

---

## 4. Horizon 2 — Loop B: the learner-facing product (months 2–4)

Goal: build the retention surface that archetype D is retained *by*. Fuel
already exists: P4 consolidation extracts decisions/actions 12-hourly
(`scripts/consolidate_memory.py`), retention/pinning backend is live
(`retention_sweep.py`), freshness endpoint is live.

| # | Feature | Builds on |
|---|---|---|
| H2-1 | **"Since your last visit"** — per-user delta digest on login: new decisions, changed docs, unanswered actions. | freshness endpoint + consolidation output |
| H2-2 | **Spaced resurfacing** — weekly "review cards" (Discord/email) resurfacing the user's *own* decisions/notes on a decaying schedule; the didactic hook for course alumni. | consolidation output + notifications v1 design |
| H2-3 | **Correction memory** — when a user corrects an answer, store the correction as a first-class, citable memory that future answers must retrieve. Today corrections only feed system tuning (golden proposals), invisible to the user. | `promote_to_golden.py` flow, artifacts store |
| H2-4 | **Cohort coverage map** — per-course syllabus template; show a learner which topics they've queried/mastered vs not. The instructor (Jay) gets a cohort view. | intent + `messages.task_tag` telemetry |
| H2-5 | **Cohort onboarding wizard** — course → tenant template (seed content, syllabus, member invites). Prerequisite: de-hardcode `hypeproof-lab` from consolidation/retention/tuning crons. | `scripts/scheduler.py`, cron scripts |

Sequencing: H2-1 → H2-2 → H2-3 are the retention core; H2-4/5 ride the first
real cohort. Each feature ships with rubric checks authored via
curator-rubric-author (new check family `P6-LEARN-*`), added to
`recipes.yaml` Tier 2 so subagents can maintain them autonomously.

**Gate H2→H3:** Ring 1 dogfood `S3 ≥ 5/8`; ≥ 3 turns/conversation average;
builder-cohort weekly return rate measurable and > 50%.

---

## 5. Horizon 3 — PMF validation with real cohorts (Q3–Q4 2026)

Aligned with the existing beachhead decision (D + A parallel, Q3 2026):

1. **Cohort pilot ×2** — each lecture cohort becomes a tenant. Instrument
   activation S3/S4 per member from day 1. Pilot 1 free; Pilot 2 introduces
   the ₩49K Starter experiment (pricing decision already leans hybrid
   seat+quota).
2. **PIPA-clean connectors P1** — voice memo + OCR per the ICP §7 pivot
   (KakaoTalk group auto-fetch is a permanent NEVER). Required for archetype
   A/B expansion, valuable for D's meeting notes.
3. **Pre-sales compliance minimums** — DPA template, privacy policy,
   audit_log, PII auto-mask (all currently unbuilt, needed before any paying
   tenant per ICP doc).
4. **Ship-gate enforcement** — no paid commitment until Ring 1 + Ring 2 pass.

**Gate H3→H4 / commercial targets:** first paying tenant; 5–7 paying tenants
by end of Q4 (per `ICP-segmentation.md` targets, shifted one quarter right if
H2 slips).

---

## 6. Kill / pivot criteria (write them down now, argue later)

- **After 2 cohorts**, if Ring 2 (`S3+` from ≥1 non-builder) has not been
  met → stop scaling sales, pivot Loop B mechanics (likely: resurfacing
  cadence or correction-memory visibility), run 1 more cohort.
- If week-4 return rate < 20% across both cohorts → the retention hypothesis
  for D is wrong; re-evaluate archetype A (clinics, 보아치과 warm lead) as
  primary beachhead before building more learner features.
- If H1 stalls below recall@3 85% after re-ranker + vector-arm fixes → freeze
  feature work, run a dedicated Ralph tuning campaign (golden-set expansion +
  ablation) before H2 exposure to real users.

---

## 7. Long-term subagent operating model (Opus/Sonnet)

Standing division of labor, consistent with the 4-tier policy in
`recipes.yaml` and the dispatch pattern in CLAUDE.md:

| Role | Model | Work |
|---|---|---|
| Parent session | Fable/Opus | Planning, review, RLS-adjacent work (Tier 3), recipes/rubric edits, gate decisions between horizons |
| curator-coder | Opus | Tier-2 work-orders: H0 wiring, re-ranker, Loop B features. Self-contained prompt + work-order JSON + cost ceiling |
| curator-reviewer | Sonnet | Adversarial review of every coder diff (5 axes), dispatched headless via `claude -p` |
| curator-fixer | Sonnet/Haiku | Tier-1 recipes: service restarts, seeds, migrations |
| curator-rubric-author | Opus | New `P6-LEARN-*` / `P*-SIGNAL-*` checks per shipped feature; human-reviewed |
| Ralph supervisor | mixed | Bulk convergence campaigns (e.g., golden-set 40→120, recall tuning) — `--max-iter 50 --cost-budget 20` |

Operating cadence:

- **Per feature**: parent writes work-order → curator-coder (Opus) →
  curator-reviewer (Sonnet) → gate (`ai-commit.sh gate` auto-bounces +
  lint-sql) → PR. New check patterns added to `recipes.yaml` at ship time so
  the failure class becomes autonomously fixable forever after.
- **Weekly**: existing tuning crons (hard-negative Mon, ablation Tue, A/B
  Wed) produce proposals; one Ralph run per week triages proposals into
  work-orders. Budget guide: ~$20/run cap, ≈$80–150/month total.
- **Monthly**: golden-set refresh + judge-holdout recalibration
  (`judge_calibration.py`, "refresh monthly" is already the stated contract).
- **Later (H4, 2027)**: graduate the highest-confidence proposal classes
  (e.g., `ab_compare` winners within guardrails) from human-gated to
  auto-apply — the last mile of Loop A. DSPy prompt optimization
  (`dspy_bootstrap.py`, currently disabled) turns on only after judge
  calibration is trustworthy.

---

## 8. Metrics dashboard (one row per horizon)

| Horizon | Timebox | Primary metric | Gate |
|---|---|---|---|
| H0 close the loop | wk 1–2 | signal rows/day, daily recall emitting | 7 green days |
| H1 retrieval depth | wk 3–8 | recall@3, MRR, faithfulness | ≥90% / ≥0.70 / ≥0.80 ×14d |
| H2 learner loop | mo 2–4 | Ring 1 S3, turns/conv, weekly return | S3≥5/8, ≥3, >50% |
| H3 PMF cohorts | Q3–Q4 | Ring 2 S3+, week-4 return, paying tenants | ≥1 non-builder, ≥20%, ≥1 |
| H4 automation | 2027 | % proposals auto-applied safely | regression-free 90d |
