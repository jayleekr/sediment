# Owned-task 1:1 — the engine clock's start button

> Ratified §10.2 (2026-05-19): the per-member owned-task map is approved as a
> working draft, **confirmed 1:1 with each member in Week-0**. The engine clock
> starts the day all 8 are confirmed (ACTIVATION_ENGINE.md §9 degraded-start).

## What "owned task" means (say this verbatim to each person)

> "Pick ONE recurring thing you currently do by grep / Drive / Discord-scroll /
> memory. For the next 4 weeks, when you need that thing, you ask Sediment
> **first** — not the old way. It's not 'use Sediment more'; it's 'stop doing
> the old thing for this one task'. If Sediment can't do it, that's a bug we
> fix in <48h, not a reason to fall back silently."

This is S3. A customer pays to *stop doing the old thing*, not to "log in".

## The 1:1 (≈10 min each, Jay runs it)

1. Confirm/replace the draft task (below). One sentence, concrete, recurring.
2. Agree the **trigger phrase** — the exact moment they'll catch themselves
   ("when I'm about to grep the repo for a past decision…").
3. They set the in-product toggle ON for it (🎯 "owned-task lookup" — already
   shipped in the chat UI; persists per browser).
4. Write it in the table. That row = their S3(a) signal source.

## Draft map (from §4 Week-0 — confirm or amend in the 1:1)

| Member | Draft owned task | Old path it replaces | Confirmed? |
|---|---|---|---|
| Jay | "where/why did we decide X?" | memory + grep `DECISIONS.md` | ☐ |
| JY | Claude-Code / coding-workflow lookups | Discord scroll + Drive | ☐ |
| Ryan | data/research-methodology citations for his pieces | re-Googling + old files | ☐ |
| Kiwon | past-column positioning / audience lookups | scrolling old columns | ☐ |
| TJ | content-workflow references | Drive + memory | ☐ |
| BH | physics-domain accuracy checks vs prior research | re-deriving + grep | ☐ |
| Sebastian | architecture-decision history (Discord-only; async) | Discord search | ☐ |
| JeHyeong | platform/spec lookups across `docs/work-specs/` | grep + asking Jay | ☐ |

## After all 8 confirmed

- Record the final map + the date in `DECISIONS.md` (engine clock = that date).
- Week-1 = concierge: Jay sits 1:1 (~30 min) while each does their owned task
  *through Sediment once*, notes the self-reported time saved (no asserted
  number — §3). `X` (S3 threshold) is the per-user median owned-task query
  rate measured this week, locked at Week-1 exit (§3a).
- Anyone unconfirmed by day 1 → their weeks shift, do not compress; the engine
  does not run hollow for them (§9 degraded-start).
