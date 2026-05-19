# Felt-need trigger + digest — spec

> ACTIVATION_ENGINE.md §9 item 6. Two jobs, both **produce content; Mother
> sends it** (project rule: Sediment never sends to Discord directly).
> Non-gated to build the producer; the Discord send is Mother's existing path.

## Job A — felt-need nudge (the "fire at the moment of need" mechanism)

**Problem it solves:** people fall back to grep/Drive *without noticing*. The
nudge catches the felt-need moment and redirects it to Sediment ONCE, so the
habit can start (M-mechanism: trigger at point of need, not a daily reminder).

**Trigger signals (cheap, no surveillance):**
- A message in a work channel matching the member's own *trigger phrase*
  (agreed in the owned-task 1:1) — e.g. JeHyeong types "where's the spec for…".
- `p5_activation` shows a member's owned-task query-rate flat-lined for >3
  days while they're active (old-path leakage, §8) → escalate in standup.

**Action (content for Mother to post, ephemeral/DM, max 1/day/person):**
> `@<member> 그거 Sediment에 물어봤어? <one-click link to /sediment with the
> owned-task toggle pre-set>`

Anti-pattern guardrails (§8): never tie to evaluation, never public shaming,
never >1/day/person, never raw-volume leaderboard.

## Job B — daily/weekly digest (feeds #sediment-dogfood)

A pure formatter, no Discord deps:

```
sediment_dogfood_digest()  ->  {channel: "#sediment-dogfood", blocks: [...]}
  daily : p5_activation.compute_activation() + GET /api/v1/vault/freshness
          → ladder_distribution, sN+_count, verdict, "vault Nh ago"
  weekly: + fix-log health (median ack-time, %closed<48h from thread state)
```

Output = a JSON content block. Mother's cron picks it up and posts (same
contract as community-manager: produce, don't send).

## Build plan (non-gated)

1. `services/sediment/scripts/dogfood_digest.py` — imports
   `validator.checks.p5_activation.compute_activation`, fetches freshness,
   emits the content JSON to stdout / a file Mother reads. **Stateless,
   testable offline** (degrades honestly when DB absent, like p5_activation).
2. Job A trigger matching is a thin rule over the member→trigger-phrase map
   from the owned-task 1:1 — built only after that map is confirmed (§10.2),
   so it is *queued behind* Week-0, not blocking it.

Gated: actual Discord posting (Mother) + the channel itself (admin). The
producer + spec are the non-gated deliverables here.
