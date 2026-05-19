# #sediment-dogfood — channel spec (M5 + M9)

> ACTIVATION_ENGINE.md §9 item 4. A **public** fix-log + signal channel.
> Purpose: make the feedback loop visibly close (principle 3) — a reported
> bug that sits >48h unacknowledged is loop death (§8 guardrail).

## Setup (Jay / Discord admin — gated, 1×)

- New channel `#sediment-dogfood` in the HypeProof Discord, all 8 + Jay.
- Pin this spec's "How to use" block as the channel topic/first message.

## How to use (pinned)

**Hit a wall?** One line, immediately, here — not DM, not silent fallback:
> `🐞 <what you asked> → <what was wrong>` (screenshot optional)

**Jay/owner replies within 48h**, always, with one of:
- `🔧 fixing — ETA <when>` (then `✅ shipped` when done, same thread)
- `⏭ won't-fix-now — <reason>, workaround: <…>`

**Every fix is announced here** when it ships (`✅ <bug> — fixed in <deploy>`).
Closing the loop in public is the mechanism; a silent fix doesn't count.

## What gets posted automatically (no leaderboard of raw volume — §8)

| Cadence | Post | Source |
|---|---|---|
| Daily 09:00 KST | ladder snapshot: `S0..S5` distribution + `Sn+ count` | `p5_activation.py` |
| Daily | freshness: "vault updated Nh ago" (⚠ if stale) | `/api/v1/vault/freshness` |
| Weekly | fix-log health: median ack-time, % closed <48h | `vault.ingest` + thread state |

**Explicitly NOT posted:** per-person query counts / a volume leaderboard
(Meta Claudeonomics burned tokens; DoorDash WeDash backfire — §8). The ladder
is about *behaviour change*, never activity theatre.

## Wiring (non-gated, can pre-build)

The daily/weekly posts are a small formatter over `p5_activation.py` output +
the freshness endpoint, posted by the existing Mother Discord path (Mother
owns Discord send per project CLAUDE.md — Sediment produces the content, does
not send). Spec for that formatter: `trigger-bot-spec.md`.
