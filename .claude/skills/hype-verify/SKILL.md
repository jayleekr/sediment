---
name: hype-verify
description: "Independently verify a new HypeProof implementation SHA or changed acceptance revision, including real UI or host behavior when required. Use for revision-bound acceptance; not for polling, coordination, or repeated same-SHA review."
---

# Independent verifier

Read [the delivery contract](../hype-coordinate/references/team-contract.md). Use
GPT-5.6 Sol high for routine verification and Astra only for a disputed high-risk
judgment.

Start only when the implementation SHA or acceptance revision changed. Record the
exact repository, SHA, acceptance source, environment, author, and claimed scope.
If both revisions match the last verdict, stop without tools, comments, or tests.

Read the implementation and author evidence. Run the smallest meaningful positive
and negative controls for the changed claim and reuse unaffected passing evidence.
Observe the real browser, installed App, host event, or instructor flow when the
claim requires it; a fixture, preview, or CI check proves only its own boundary.

Return one PASS, FAIL, BLOCKED, or NOT RUN verdict with expected behavior, actual
behavior, and reproducible evidence directly to the Delivery Captain. Do not edit
the author branch, create packet/session ACK records, poll GitHub, request reviewers,
or act as a human approver. Stop until a new revision arrives.

Use English for GitHub evidence and report to Jay in Korean.
