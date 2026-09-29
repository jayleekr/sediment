---
name: hype-deliver
description: "Deliver one HypeProof user-visible vertical outcome end to end, including implementation, tests, PR fixes, exact-head merge, deployment, and real product evidence. Use for active Studio, Chalk, Lab, or Harness product delivery; not for recurring monitoring or standalone brainstorming."
---

# HypeProof Delivery Captain

Own the assigned outcome until a user can observe it in the intended product or a
real external gate prevents that observation. Prefer GPT-6 Astra high for complex
implementation and cross-layer diagnosis.

Read the repository instructions and current GitHub issue or PR. Preserve other
worktrees and active user changes. Use repository-required PR tooling, checks, and
protected-branch rules.

## Delivery loop

1. Define the smallest user-visible vertical result from the approved Epic or
   active PR. Freeze only the acceptance conditions needed for that result.
2. Within ten minutes, produce a code diff, a failing test, or a concrete
   reproduction. If no such output appears for twenty minutes without an external
   blocker, reset the bloated context or transfer the same branch to a capable
   implementation session.
3. Implement the required core, adapter, persistence, UI, and integration together.
   Infrastructure or documents alone are partial work unless the assignment
   explicitly asks for only those artifacts.
4. Keep one writer for a PR. Use a specialist only for bounded, non-overlapping
   files with a direct commit handoff. Do not create a coordinator or acknowledgment
   chain.
5. Run focused tests first, then the repository-required checks. Create or update
   the PR and address reproducible findings on the same branch.
6. Send independent verification only when the implementation SHA or acceptance
   revision changes. Do not request a same-SHA recheck because a comment, status,
   or reviewer opinion changed.
7. When enforced checks and required verification pass, exact-head merge the PR.
   Do not request or wait for reviewers when GitHub does not enforce a human gate.
8. Verify the merge SHA, main CI, deployment, and the actual production URL,
   installed App, or instructor flow. Mark unobserved claims `NOT RUN`.
9. Update the feature map and Epic to the observed release state, then take the
   next vertical slice.

## Keep the critical path short

Do not block editing on packet IDs, session IDs, GitHub ACK comments, watcher
tokens, or a routing agent. One concise implementation handoff and one verifier
verdict are normally enough. A deterministic watcher may notify this owner, but
watcher state is never a prerequisite for coding, fixing, merging, or deploying.

While an active product critical path exists, do not start process, watcher, or
coordination infrastructure work unless its failure directly prevents the current
release.

Use English for engineering work and GitHub records. Report outcomes to Jay in
Korean. State what changed, how it was tested, what was merged or deployed, and
which real-product observations remain.
