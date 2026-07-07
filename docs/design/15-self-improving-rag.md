# Self-Improving RAG — 8-Week Plan

> Status: design, not yet implemented
> Premise: Sediment already has 50% of the pieces (golden set, validator,
> events table, Ralph loop). We close the loop, not rebuild it.
> Owner: Jay.

---

## 0. Premise — what "self-improving" means here

Not "the LLM rewrites itself overnight." Concretely:

1. **Every user query becomes a data point.** Pass/fail, implicit signals
   (re-ask, copy, dwell), explicit (thumbs), routing intent, latency,
   cost — all flow into `events` + `mcp_call_log` + `usage_events`.
2. **Every prod failure becomes a future test.** One-click "this answer
   was wrong" → row appended to `golden_queries.yaml`. CI runs every PR
   against the expanded set.
3. **Every commit is gated on the eval bar.** Recall@3, faithfulness,
   intent accuracy thresholds in `rubric.yaml` block merges below
   baseline. Drift is impossible without explicit acknowledgement.
4. **Every week, the system tunes itself.** DSPy bootstrap on the
   growing golden set; hard-negative re-embedding pass; chunking
   parameter ablation. Output: a versioned config that beats last week's
   numbers on the same questions.

The loop closes when (4) feeds back into prod and (1) measures the
improvement. Today (1)–(3) exist in pieces; (4) is missing entirely.

---

## 1. Current state inventory (what we already have)

From the internal survey:

| Capability | Where | What it gives us |
|---|---|---|
| Golden query set (40 KO/EN cases, expected intent + ideal refs) | `services/sediment/validator/golden_queries.yaml` | Recall@K baseline, regression detection |
| Intent golden (31 cases) | `services/sediment/tests/test_ask_intent_golden.py` | Router fidelity |
| Live runner | `services/sediment/scripts/run_golden_against_prod.py` | E2E from prod-real input → prod-real answer |
| Validator framework (5 check types, Phase 0-5, severity-weighted scoring) | `services/sediment/validator/rubric.yaml` + `validator/checks/lib_rag.py` | Recall@K, MRR, latency histograms |
| Phase 5.5 dogfood gate (10 measurable criteria) | `validator/checks/p5_dogfood.py` (spec `PHASE_5_5_DOGFOOD_GATE.md` absent) | Quality contract (NPS, faithfulness, thumbs-up, ...) |
| Event store + feedback POST | `events` + `mcp_call_log` + `/api/v1/feedback` | Per-query traces, ratings (skeleton wired) |
| Ralph supervisor + LEARNINGS.md | `harness/ralph/` | Autonomous loop that already learns from failure (35KB / 200+ patterns) |
| Daily P3 cron with Discord regression alerts | `make p3-cron-install` | Already pings when checks regress |

The bones are there. The wiring isn't.

---

## 2. What's missing — the 10 gaps from the internal sweep

| # | Gap | Impact |
|---|---|---|
| 1 | **LLM-as-judge** for faithfulness / answer relevancy | CI passes if SQL recall>0 even when the LLM made it up |
| 2 | **Cost dashboard** on `usage_events` | We can't optimize what we don't see |
| 3 | **Prompt-injection red-team** (Promptfoo) | Security regression undetected |
| 4 | **Live feedback loop into tuning** | Ratings just sit in the events table |
| 5 | **/orbit wiring into Sediment harness** | Self-refining skill exists, not pointed at sediment |
| 6 | **A/B harness** for ranking-algo / chunking variants | Hard to know if a change actually helped |
| 7 | **Tracing backend** (Langfuse / LangSmith) | Trace-level debugging is print-statement-driven |
| 8 | **Nightly RAGAS** | Only on-demand today |
| 9 | **Memory-consolidation eval** (cron extracts decisions, but is it right?) | Distill/consolidate output unvalidated |
| 10 | **NPS automation** | Required by gate criterion #9, manual today |

---

## 3. Phased plan

### Phase 1 — Close the CI eval loop (weeks 1-2)

**Goal**: every PR runs RAGAS faithfulness + answer relevancy against a
50+ case golden set, with a hard threshold. Drift = blocked merge.

**Deliverables**:

- `services/sediment/validator/checks/p2_faithfulness.py` — wraps RAGAS
  faithfulness + answer_relevancy + context_recall + context_precision.
  Reads `golden_queries.yaml`, runs against in-process platform, emits
  `output/validation/p2-faith-iter*.json`.
- `validator/rubric.yaml` — add `P2-FAITH-*` checks with thresholds:
  - `faithfulness ≥ 0.75` (blocker)
  - `answer_relevancy ≥ 0.80` (blocker)
  - `context_recall ≥ 0.80` (major)
  - `context_precision ≥ 0.70` (minor)
- `.github/workflows/cli-tests.yml` — add a "Faithfulness gate" job after
  the existing UT + IT layers. Block merge if any blocker check
  regresses from main.
- `services/sediment/tests/test_ask_intent_golden.py` — expand from 31
  to **100 cases**. Each row from the last 30 days' production failures
  appended (see Phase 4 for the automated path; until then, manual
  weekly batch via a script that reads `events` table for
  `feedback.message.rating=-1` plus low-confidence intent calls).

**Exit criteria**:
- Three consecutive PRs gated by faithfulness (a known-bad commit MUST
  be blocked when run through the gate)
- Baseline numbers documented: faithfulness ≥ 0.80 today against
  current 40-query golden (we measure, not aspire)

**Risks**:
- RAGAS uses an LLM judge → cost. Budget: $0.50 per CI run.
- Faithfulness scoring is non-deterministic. Mitigation: run each query
  3× and use median (per the LLM-judge research, see §5).

---

### Phase 2 — Implicit signal capture (weeks 2-3)

**Goal**: every user interaction generates an unambiguous quality signal
in the `events` table. No UI work required for the data path.

**Deliverables**:

- `lab_lib/signal_capture.py` — middleware that derives signals:
  - `re_ask` — same member submits a near-identical query (Levenshtein
    ≤ 0.3) within a single conversation. → `events.kind = 'signal.reask'`.
    Means previous answer was unsatisfying.
  - `copy_event` — `POST /api/v1/events/cite-export` already exists
    (per cross-repo map); wire it through. Means answer was useful.
  - `dwell` — time from response to next action. Long dwell + no
    follow-up = success; short dwell + re-ask = failure.
  - `thumbs` — already wired via `/api/v1/feedback`.
- `services/sediment/validator/checks/p2_signal_quality.py` — aggregates
  signals into a per-week quality score per intent. Tracked in
  `output/dogfood/<YYYY-MM-DD>.json` alongside existing metrics.
- `frontend/app/sediment/c/[id]/page.tsx` — add the thumbs/copy buttons
  the LEARNINGS file flagged as incomplete (no other UI changes).
- New event kind: `signal.session_end` — emitted when a conv has been
  idle ≥ 5 min. Final per-conv aggregate: # turns, # thumbs, # copies.

**Exit criteria**:
- 7-day window: ≥80% of `query` events have at least one downstream
  signal (re_ask / copy / thumbs / session_end)
- `mcp_call_log` × `events` join answers: "for this query type, what's
  the satisfaction rate?"

**Risks**:
- Re-ask detection false positives (legit follow-up "tell me more" ≠
  failure). Mitigation: include intent in match; only flag re-ask
  when intent is the same AND query is structurally similar.

---

### Phase 3 — LLM-as-judge with swap-and-average (weeks 3-4)

**Goal**: nightly judge that scores yesterday's production answers and
surfaces regressions before users do.

**Deliverables**:

- `services/sediment/validator/judge/grounding.py` — pair-wise
  faithfulness judge (Claude Sonnet judging Claude Haiku output, per
  the research recommendation for structurally-different judge family).
  - Swap-and-average: each pair runs twice with reversed order.
  - 5-10 anchor examples at score levels 1/3/5 to prevent central
    tendency compression.
  - Outputs scalar [0, 5] + structured reason.
- `services/sediment/scripts/judge_daily.py` — cron at 06:00 KST: pull
  yesterday's `events.kind='query'` rows, replay the answers through the
  judge, write scores back to `events.payload.judge_score`. Discord
  alert if daily median drops > 0.5 from rolling 7-day median.
- `services/sediment/validator/checks/judge_calibration.py` — monthly
  human-labeled holdout (20 cases). Computes Krippendorff α against
  judge scores. Alert if α < 0.80.

**Exit criteria**:
- 4-week judge run; α stays ≥ 0.80
- Discord alerts fire on a real regression (we'll test by deliberately
  ingesting one bad commit's worth of data and confirming the alert)

**Risks**:
- Judge cost. Estimate: 100 queries/day × $0.005 = $0.50/day = $15/mo.
  Acceptable.
- Self-preference bias even with cross-family. Mitigation: rubric
  forces structured criteria, not free-form.

---

### Phase 4 — One-click "production failure → golden case" (weeks 4-5)

**Goal**: when a user (or the judge) flags a failure, the case becomes a
permanent regression test within 60 seconds.

**Deliverables**:

- `sediment learn add <conv_id>` CLI subcommand — fetches the
  conversation, opens an editor with a pre-filled YAML row (query,
  observed_answer, expected_intent, ideal_refs left blank for human
  fill), validates the row, appends to `golden_queries.yaml`, opens a
  PR via `gh pr create`.
- `frontend/app/sediment/c/[id]/page.tsx` — on a low-thumbs answer,
  show "Report as failure" → calls
  `POST /api/v1/feedback/promote-to-golden` with the conv_id. Server
  uses the same logic as the CLI's `learn add`.
- `services/sediment/validator/checks/golden_set_growth.py` — track
  golden set size over time. Alert if no growth in 14 days (means
  feedback flow is broken).

**Exit criteria**:
- 1 user-reported failure successfully PR'd via the web UI within a
  single browser session, end-to-end
- Golden set grows ≥ 1 case/week on average

**Risks**:
- Spam (someone marks everything as failure). Mitigation: rate-limit
  promote endpoint to 5/day/member; admin approves PRs.

---

### Phase 5 — Automated retrieval improvement (weeks 5-8)

**Goal**: weekly the system *actually changes* — chunking params,
prompts, embedding model — in measurable ways, gated by the golden set.

**Deliverables**:

- `services/sediment/scripts/dspy_bootstrap.py` — once a week, runs
  DSPy `BootstrapFewShot` over the chat compose prompt against the
  current golden set. Cost: ~$0.50/run. Output: a candidate prompt
  variant + measured delta vs current.
- `services/sediment/scripts/hard_negative_mining.py` — for every
  golden case where context_recall < target, the top-K retrieved-but-
  unused chunks become hard negatives. Accumulate weekly into
  `output/hard_negatives.jsonl`. Use to drive periodic embedding
  re-tuning (Cohere Embed v3 custom training, or
  sentence-transformers).
- `validator/ablation/` — chunking parameter grid:
  `chunk_size ∈ {300, 500, 800}` × `overlap ∈ {0, 50, 100}`. Single
  weekly job runs the full grid against the golden set, picks the
  Pareto frontier (recall vs cost). Output: a recommendation, not an
  auto-apply.
- A/B harness: feature-flagged retrieval variant — half the queries
  go through variant A, half through variant B. After 7d, compare
  judge scores + thumbs rates. Decision: human-approved promotion.

**Exit criteria**:
- One measured improvement landed via DSPy bootstrap (e.g., faithfulness
  +0.05 with prompt variant N)
- One measured improvement landed via embedding refresh
- Both are visible in the rolling 30-day Phase 5.5 dogfood JSON

**Risks**:
- DSPy compile cost grows with golden set size. Mitigation: subsample
  to 50 if set > 200.
- Embedding re-tune breaks RLS (different vectors, same chunks). RLS
  cross-tenant tests already in CI catch this — keep them mandatory.

---

## 4. Cross-cutting infrastructure

### 4.1 Use-case taxonomy (the "U" in self-improving)

Every signal needs an intent tag so we can measure per-use-case:

| Use case | Verb / route | Quality metric | Signal source |
|---|---|---|---|
| Ask synth | `sediment ask`, web chat | faithfulness, recall, thumbs | judge, /feedback, copy |
| Direct search | `sediment search`, /library | MRR, click-through | mcp_call_log latency + downstream click |
| Direct read | `sediment read`, /library/ref | 404 rate | mcp_call_log status_code |
| Recency lookup | `sediment recent`, /library?date_from | freshness lookup intent fidelity | golden intent set |
| Meeting prep | `sediment ask` (proxy) | task completion (did user leave with the doc?) | session_end + downstream cite-export |
| Decision audit | `sediment ask` (proxy) | decision found in citations | judge structured criteria |
| Ingest | webhook, cron | ingest success rate, chunk count delta | usage_events |
| Memory consolidation | cron `consolidate` | extracted decision accuracy | weekly human spot-check |

Each row gets its own golden subset. Each phase's metrics are
broken down by row.

### 4.2 Where data goes (the data plane)

```
                  (web UI / CLI) ──── thumbs, copy, re-ask, dwell ─┐
                       │                                            │
                       ▼                                            ▼
              /api/v1/* (FastAPI)                          /api/v1/feedback
                       │                                            │
                       ▼                                            ▼
            ─── mcp_call_log INSERT ───              ─── events INSERT ───
                       │                                            │
                       └────────────────┬───────────────────────────┘
                                        ▼
                              hourly aggregator (cron)
                                        │
                                        ▼
                          output/dogfood/<date>.json   ← already exists
                                        │
                                        ▼
                       Discord daily summary  ← already exists for P3
                                        │
                                        ▼
                       weekly /orbit (autonomous loop)
                                        │
                                        ▼
                    proposes changes → human approval → merge → gates
```

Nothing new needed at the data layer — all tables exist. The hourly
aggregator + autonomous loop are the new pieces.

### 4.3 Orbit as the orchestrator

The `/orbit` skill (in user's global skills list) already implements:
`Collect → Create → Validate → Monitor → Refine`. We point it at
Sediment:

- **Collect**: read last 7d of `events` + `mcp_call_log` + golden-set
  scores
- **Create**: propose a change — prompt variant, chunk param, golden
  case addition
- **Validate**: run the full validator suite against the proposed
  change in a worktree
- **Monitor**: if validate passes baseline + 1σ, open a PR
- **Refine**: on next iter, read PR review comments + post-deploy
  metrics, adjust

This gives us a *continuous* self-improvement loop without rebuilding
infrastructure.

---

## 5. Metrics — how we know it's working

### North-star (already in Phase 5.5 gate)
- Faithfulness ≥ 0.80
- Recall@3 ≥ 27/40 (current baseline)
- Thumbs-up rate ≥ 0.70
- E2E flake ≤ 0.10
- DAU ≥ 5 for 14/28 days

### Loop-health (new)
- Golden set growth: ≥1 case/week
- Implicit signal coverage: ≥80% of queries
- Judge α (vs human): ≥0.80
- Time-from-failure-to-golden-case: median ≤ 24h
- Weekly ablation cost: ≤ $20

### Anti-metrics (what would make us pause)
- Faithfulness drops > 0.05 week-over-week → emergency halt of
  auto-tuning
- RLS cross-tenant tests fail at any point → revert and PD
- Cost > $50/week on eval infrastructure → cost review

---

## 6. Sequencing — what we ship first

Two-month rough schedule:

```
Wk 1-2  ── Phase 1 (RAGAS in CI)             ◀── highest leverage, ship first
Wk 2-3  ── Phase 2 (Implicit signals)        ◀── unblocks 3, 4
Wk 3-4  ── Phase 3 (LLM judge)               ◀── needs signals + golden set
Wk 4-5  ── Phase 4 (One-click promote)       ◀── makes the loop circular
Wk 5-8  ── Phase 5 (Auto-tuning)             ◀── needs everything above
```

Each phase has its own exit criteria. We don't start Phase N+1 until
Phase N's exit criteria hold for 5 consecutive days.

---

## 7. Decisions we need to make (open)

| # | Question | Default |
|---|---|---|
| 1 | RAGAS LLM model — Claude Haiku ($) or Sonnet ($$$$)? | Haiku per CI run, Sonnet for nightly only |
| 2 | Auto-apply prompt variants from DSPy, or always human-gate? | Always human-gate (sediment is small enough) |
| 3 | Open source the golden set? | No — internal-only, contains team queries |
| 4 | Promote-to-golden requires admin approval, or any member can append? | Any member → admin merges PR |
| 5 | Judge runs on PROD data (privacy concern?) or replayed in dev? | Replayed in dev — never sends prod-bytes to a fresh LLM call |

---

## 8. What this is NOT

- Not a research project. Every phase ships measurable value to users.
- Not a rebuild — leverages golden_queries, validator, events table,
  Ralph, /orbit.
- Not "the LLM rewrites the system" — every change is git-tracked,
  reviewable, revertable.
- Not unlimited spend — total infra budget for eval/tuning ≤ $50/wk.

---

## 9. References

- DSPy + LiveRAG 2025 benchmark — semantic similarity 0.771 (compiled)
  vs 0.668 (baseline)
- Self-RAG (arXiv 2310.11511) — retrieval gating pattern
- Braintrust / RAGAS / DeepEval — 2026 dominant CI eval stack
- Glean Waldo (Apr 2026) — feedback-driven retrieval improvement, agentic
- LLM-as-judge: swap-and-average + Krippendorff α ≥ 0.80 (April 2026
  rubric-eval paper)
- Hard negative mining (DocReRank, arXiv 2505.22584)
- KG + RAG hybrid (Harness blog, NetApp blog 2025)

---

*Last updated: 2026-05-22. Lives next to other design docs under
`docs/design/`. Updates here happen in PRs that touch the matching code.*
