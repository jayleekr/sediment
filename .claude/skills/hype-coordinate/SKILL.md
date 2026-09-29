---
name: hype-coordinate
description: "Recover or inspect the legacy HypeProof five-session watcher and queue. Use only for existing control-plane recovery; new product delivery uses hype-deliver instead."
---

# Legacy coordinator recovery

Read [the current delivery contract](references/team-contract.md). This skill exists
to inspect or safely stop the legacy `delivery_delta.py`, `watch_delivery.py`, and
`wake_role.py` state left by earlier five-session runs.

Do not use this role as the normal entry point for new product work. Do not put a
coordinator between an implementation owner and verifier, wait for GitHub ACK or
watcher-token state before work, repeat a same-SHA verification, or turn watcher
repair into the product critical path.

For recovery, inspect the running process, lock, pending revision, target composer,
and active writer. Preserve user input and uncommitted changes. Remove duplicate
loops, prevent duplicate dispatch, and hand the actual GitHub URL and revision to
the Delivery Captain. The Captain owns implementation, merge, deployment, and real
product evidence.

If legacy state cannot be retired immediately, keep the watcher notification-only
in practice: acknowledge or suppress a duplicate once an active writer or exact
external gate is observed. Do not create status comments merely to satisfy the old
ledger.

Use English for engineering records and report the recovery result to Jay in Korean.
