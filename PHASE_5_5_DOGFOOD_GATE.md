# Phase 5.5 Dogfood Gate — Measurable Criteria

> Status: **spec drafted, awaiting first-week measurement**
> Authors: Jay (PM) + Sediment harness
> Last updated: 2026-05-09
>
> Gate owner: Jay decides go / no-go for Phase 6 (external customer onboarding)
> based on the 10 criteria below. Each has a measurable threshold and a
> measurement source. Sediment's `validator` already produces some of these
> automatically; the rest require lightweight instrumentation that this doc
> specifies.

---

## Why a gate?

Per `DECISIONS.md` and `SESSION_HANDOFF_2026-05-08.md` §8, Phase 5.5 = 4-week
dogfood by 8 Lab members (Jay + JY + Ryan + Kiwon + TJ + BH + Sebastian +
JeHyeong). Without an explicit pass/fail rubric, "ready for external customers"
becomes a vibes call. This document fixes that.

The gate fires once at the end of week 4. It does NOT block week-by-week
iteration — only the Phase 5.5 → Phase 6 transition. Ralph supervisor and
validator continue to run regardless.

---

## The 10 criteria

Each criterion has:
- **Threshold** — pass/fail line
- **Source** — where the number comes from (validator JSON / SQL probe / Discord export / manual survey)
- **Auto-measurable** — can `validator/checks/p5_dogfood.py` (TBD) compute it without human input?

### A. Functional readiness (4)

| # | Criterion | Threshold | Source | Auto |
|---|---|---|---|---|
| **1** | P2 validator score | ≥ 95% (15 of last 20 runs) | `output/validation/P2-iter*.json` rolling window | ✅ |
| **2** | E2E flow flake rate | ≤ 10% per flow | `e2e_runner.py` flake_rate_pct field | ✅ |
| **3** | Mean answer faithfulness vs golden 40 | ≥ 0.75 | `validator/checks/p1_golden.py` cosine score | ✅ |
| **4** | RLS verify_rls cross-tenant probe | 0 leaks across 7 days | `verify_rls.py` daily cron output | ✅ |

### B. Adoption (3)

| # | Criterion | Threshold | Source | Auto |
|---|---|---|---|---|
| **5** | DAU among the 8 seed members | ≥ 5 distinct users / day for ≥ 14 of 28 days | `messages` table + `member_id` distinct count | ✅ |
| **6** | Total queries logged in week 4 | ≥ 200 (≥ 25/member avg) | `events` table count where kind=query | ✅ |
| **7** | Mean session conversation length | ≥ 3 turns | `messages` group by conv_id | ✅ |

### C. Quality (3)

| # | Criterion | Threshold | Source | Auto |
|---|---|---|---|---|
| **8** | Thumbs-up rate on assistant messages | ≥ 70% (where rated) | `events.feedback.message` payload.rating | ✅ |
| **9** | NPS-style "would recommend to a colleague" | ≥ 7/10 (median, n ≥ 6) | One-question survey week 4 (Discord poll) | ❌ manual |
| **10** | Critical incidents (data leak, billing wrong, prod down) | 0 over 28 days | `LEARNINGS.md` pattern=critical_incident search | ✅ |

---

## Mapping to existing artifacts

| Source | Already exists? | Where |
|---|---|---|
| validator P2 score | ✅ | `output/validation/P2-iter*.json` |
| E2E flake rate | ✅ | embedded in P2 result, `actual.flake_rate_pct` per flow |
| RLS probe | ✅ | `scripts/verify_rls.py`, exit 0 = pass |
| DAU / queries / turns | ⚠️ table exists, **need aggregator** | `validator/checks/p5_dogfood.py` (new) |
| Feedback ratings | ⚠️ table exists, **frontend wires it on /curator/c/[id]** but no rollup | needs `routers/feedback.py` + aggregator |
| Critical incidents | ✅ via LEARNINGS grep | `harness/ralph/LEARNINGS.md` |
| NPS survey | ❌ | manual Google Form / Discord poll, week 4 day 5 |

---

## Implementation plan (when measurement starts)

Phase 5.5 begins when Jay flips `feature_flags.dogfood_gate_active = true` in
the platform config. From that day, a daily cron runs the gate aggregator.

### Step 1 — Aggregator script (1 hour to write)

`products/sediment/services/sediment/validator/checks/p5_dogfood.py`:

```python
async def check_p5_dogfood_full() -> dict:
    """Compute all 10 criteria from live DB + filesystem. Returns rubric-style result."""
    out = {"criteria": {}, "all_passed": True}
    out["criteria"]["1_p2_score"] = _rolling_p2_score(window=20, threshold=95.0)
    out["criteria"]["2_e2e_flake"] = _e2e_flake_check(threshold=10.0)
    out["criteria"]["3_faithfulness"] = await _golden_faithfulness(threshold=0.75)
    out["criteria"]["4_rls_leaks"] = _rls_zero_leaks(days=7)
    out["criteria"]["5_dau"] = await _dau_count(min_dau=5, min_days=14, window=28)
    out["criteria"]["6_total_queries"] = await _query_count(week=4, min_total=200)
    out["criteria"]["7_avg_turns"] = await _avg_turns(min_turns=3.0)
    out["criteria"]["8_thumbs_up_rate"] = await _thumbs_up_rate(min_rate=0.70)
    out["criteria"]["9_nps"] = _read_nps_file_or_skip()  # manual entry
    out["criteria"]["10_incidents"] = _learnings_critical_count(days=28, max_count=0)
    out["all_passed"] = all(c["passed"] for c in out["criteria"].values())
    return out
```

Output → `output/dogfood/2026-05-09.json` (one snapshot per day).

### Step 2 — Daily cron (5 min to write)

launchd plist `~/Library/LaunchAgents/com.hypeproof.sediment.dogfood.plist`:
- StartCalendarInterval: 09:00 daily
- ProgramArguments: `bash -c 'cd ... && .venv/bin/python -m validator.checks.p5_dogfood > output/dogfood/$(date -I).json'`
- Stdout/stderr to `output/dogfood/cron.log`

### Step 3 — Discord summary (5 min)

Once a Discord webhook is configured, post the daily snapshot to
`#content-pipeline` so Lab members can see whether the gate is trending up.
Message format:
```
📊 Dogfood Gate Day 14 of 28
  P2 score:        96.3% ✅
  E2E flake:       4.2%   ✅
  Faithfulness:    0.78   ✅
  DAU:             6/8    ✅
  Queries (week):  47/200 ⚠️ on track for 188 — pace +6% needed
  Thumbs-up:       72%    ✅
  ...
```

### Step 4 — Week-4 NPS form (manual, day 25)

Post a 1-question Discord poll: "Would you recommend Sediment to a colleague?
(0–10)". Collect ≥ 6 responses. Median = criterion 9.

---

## Decision matrix

| All 10 pass | 8–9 pass | ≤ 7 pass |
|---|---|---|
| **GO Phase 6** — pick 1 outside customer (donga / academy / consulting / CERN — see DECISIONS.md §11.3) and start onboard | **CONDITIONAL** — extend Phase 5.5 by 2 weeks targeting the failing criteria, retest | **NO-GO** — root-cause analysis week, may extend Phase 5.5 by full month or de-scope |

The decision is Jay's. The gate exists to make the data unambiguous, not to
remove judgment. Examples of legitimate overrides:

- All 10 pass but Jay senses adoption is shallow ("yeah it works but no one
  reaches for it") → still NO-GO, fix engagement before exposing externally.
- 7/10 pass but the 3 failures are the auto-measurable ones that are easy to
  fix in 1 week (e.g. flake rate, DAU just below 5/8) → CONDITIONAL ok.

---

## Why these particular numbers

- **P2 ≥ 95%**: validator gates at 95 for "release-ready" elsewhere in the
  project (rubric.yaml). Aligns with that bar.
- **Flake ≤ 10%**: standard CI flake threshold (Google's blog calls anything
  >10% "broken").
- **Faithfulness ≥ 0.75**: golden-set faithfulness scores ≥ 0.75 indicate the
  model is using the citations rather than freelancing. Below this customers
  see hallucination rate that's not differentiated from generic ChatGPT.
- **DAU 5/8 for 14/28 days**: more than half the seed cohort using more than
  half the days. Below this the dogfood ISN'T proving habit formation.
- **Thumbs-up ≥ 70%**: same threshold as ChatGPT internal benchmark.
- **0 critical incidents**: any data leak or billing error in the dogfood
  cohort = automatic NO-GO regardless of other 9. Trust signal for paid
  customers.

---

## What this doc is NOT

- **Not a launch checklist** — that's `DECISIONS.md` §11.18 (compliance) +
  Phase 6 onboard runbook (TBD).
- **Not a SLA** — internal threshold, not a contractual promise.
- **Not eternal** — these criteria reflect 2026-05 priorities; revisit before
  Phase 7+ (multi-tenant SaaS) where customer health metrics replace internal
  ones.

---

*Reference: SESSION_HANDOFF_2026-05-08.md §8 Phase 5.5+ items 11–16 list the broader Phase 5.5 work; this doc specifies the exit gate only.*
