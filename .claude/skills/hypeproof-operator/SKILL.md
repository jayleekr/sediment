---
name: hypeproof-operator
description: "Use this skill for HypeProof-only repository operations: governance policy, member permissions, branch protection, security incidents, docs consistency, Vercel/GitHub deploy checks, review requests, PR monitoring, merge sequencing, and release repo handling for hypeproof-harness, hypeproof-studio, hypeprooflab, sediment, and their release repos. This skill must scope actions to HypeProof-managed projects only and must not operate on personal blogs, side projects, or unrelated repos unless the user explicitly gives a separate repo-specific override."
metadata:
  short-description: "HypeProof-only repo governance, security, review, merge, and deploy operations"
---

# HypeProof Operator

This skill keeps Codex focused on HypeProof work only. Treat it as the operating runbook for requests like "all repos", "review everything", "merge when approved", "fix docs", "check deploy", "branch protection", "security issue", or "member permissions" when the conversation is about HypeProof.

## Scope Gate

Before reading, mutating, reviewing, or merging anything, resolve the repo scope.

Allowed HypeProof scope:

- `jayleekr/hypeproof-harness`
- `jayleekr/hypeproof-studio`
- `jayleekr/hypeprooflab`
- `jayleekr/sediment`
- `jayleekr/hypeproof-studio-releases`
- `jayleekr/sediment-cli-releases`
- Any future repo already listed in `hypeproof-harness/policy/repos.yaml` whose `products` or `lifecycle` clearly belong to HypeProof product, governance, or release operations.

Excluded by default:

- Personal blog/site repos such as `jayleekr/jayleekr.github.io`.
- Side projects, experiments, archives, or unrelated repos even if they are under the same GitHub account.
- Repos not present in `policy/repos.yaml`, unless the user explicitly asks to onboard that repo into HypeProof governance first.

If the user says "all repos", interpret it as "all allowed HypeProof scope", not all repositories accessible to the GitHub token.

## Control Plane

Use `jayleekr/hypeproof-harness` as the source of truth.

- Repository list and desired settings: `policy/repos.yaml`
- Members and permissions: `policy/members.yaml`
- Policy profiles: `policy/profiles/*.yaml`
- Shared agent guidance: `docs/AGENT-GUIDE.ko.md`
- Shared skills: `skills/`

Never patch vendored copies inside consumer repos when the canonical file lives in harness. Patch harness, then sync or open downstream PRs as needed.

## Default Workflow

1. Scope: identify the exact HypeProof repo set and reject accidental personal/side repos.
2. Inspect: read policy, current PRs/issues, CI, deploy state, and local dirty worktrees before mutating. Skip issues already carrying a fresh `wip` claim — another session owns them.
3. Track: create or update a GitHub issue for governance/security/docs/deploy work unless the task is a tiny read-only check.
4. Claim: before touching code, take the issue — `gh issue edit <N> --add-label wip --add-assignee @me` plus a comment naming your session and branch. Drop `wip` when done; a merged `Closes #<N>` PR releases it implicitly. A claim older than 24h (by its comment timestamp) is stale and reclaimable. Convention only — no CI check enforces it (WEEKLY-LOOP.ko.md §6.0).
5. Branch: use `fix/`, `feat/`, `docs/`, or `chore/`; avoid direct `main` pushes.
6. Change: keep edits narrow and aligned with the target repo.
7. Validate: run the smallest meaningful local tests plus repo-specific checks.
8. PR: include `Closes #...` or a clear issue link, explain risk, and do not request reviewers for Jay-hosted HypeProof work.
9. Integrate: after the exact head passes required checks and applicable evidence gates, squash-merge it without waiting for a review, then watch main CI, deployment, and the live surface until the result is verified or a concrete external condition blocks it.

## Governance Tasks

For branch protection, visibility, collaborator, Actions, secret scanning, or repo settings:

```bash
python3 scripts/repo-governance/audit.py --offline
python3 scripts/repo-governance/audit.py --all
python3 scripts/repo-governance/apply.py --repo owner/name --module repo_settings --apply
python3 scripts/repo-governance/apply.py --repo owner/name --module collaborators --apply
python3 scripts/repo-governance/apply.py --repo owner/name --module branch_protection --apply
```

Use `--module` narrowly. If GitHub personal-account plan limits block branch protection or private repo controls, record it in an issue instead of pretending it is fixed.

Public-by-default operating stance:

- Public repos are acceptable for HypeProof product/governance work.
- Code contribution should still be restricted by collaborator permissions and branch protection.
- Pending collaborator invitations are not fixed until the invitee accepts; track them as external blockers.

## Finding What Needs Review

Two harnesses answer two different questions. Run both — neither alone is complete.

```bash
# 1. PRs that explicitly requested me (plus, by default, PRs blocked on my approval)
python3 scripts/hype-review/review.py --mine

# 2. Every open PR in policy repos with ready/waiting/blocked + blockers
python3 scripts/hype-merge/monitor.py
```

`review.py` is reviewer-centric and `monitor.py` is repo-centric. Use the monitor
whenever the task is "check everything" — it is the only view that shows PRs
belonging to other reviewers, draft PRs, and PRs stuck on checks rather than review.

Sweep order that works: monitor for the full picture, `review.py --mine` for the
worksheet on each PR actually assigned to me, then act.

## Review Requests

Jay's standing instruction for HypeProof work is: do not request reviewers. This
applies to ordinary product, content, documentation, test, and deployment PRs in
the allowed scope, including PRs created by an agent. Do not call
`hype-pr request-reviewers`, add reviewers during PR creation, or wait for a
social approval that GitHub does not enforce. Remove stale pending requests when
they would leave the wrong operational signal.

An explicit per-PR instruction from Jay may re-enable review for that PR. If
GitHub technically enforces a non-author approval or a control-plane rule blocks
the merge, record the exact enforced gate and keep doing independent work. Do not
manufacture an approval, weaken protection, or send a review request without that
separate instruction.

## Merge And Auto-Merge

Jay's standing instruction grants merge authority for ordinary HypeProof delivery.
Do not ask Jay to press the merge button or wait for a review request. Squash-merge
the exact current head as soon as required CI passes, applicable X2 evidence is
current, dependency order is resolved, and no explicit hold, failed check,
changes-requested state, conflict, or unresolved material product/security/business
decision remains. Then verify the merge SHA, main CI, deployment, and live behavior
that the change claims.

Safe auto-merge policy:

- Enable auto-merge only for HypeProof repos whose harness policy explicitly allows it.
- Never enable auto-merge for excluded repos such as personal blogs.
- Prefer an exact-head immediate merge once gates pass. Use auto-merge only when a
  repository-enforced check is still running and the policy allows reservation.
- Do not auto-merge release repos unless a release-specific policy explicitly permits it.

If `scripts/hype-merge/monitor.py` or `scripts/hype-merge/automerge.py` exists, use it. Otherwise inspect with:

```bash
gh pr list --repo owner/name --state open --json number,title,author,reviewDecision,mergeStateStatus,autoMergeRequest,statusCheckRollup
gh pr view <number> --repo owner/name --json reviews,reviewDecision,mergeStateStatus,statusCheckRollup,autoMergeRequest
```

Before enabling auto-merge, verify the head SHA and use `--match-head-commit` where possible.

### Merge trains: expect repeated BEHIND

Product repos require the branch to be current, so **merging one PR pushes every
other open PR in that repo to BEHIND**. Refresh and re-read the exact head for
each PR:

```bash
gh pr update-branch <n> --repo owner/name
gh pr view <n> --repo owner/name --json headRefOid,mergeStateStatus,statusCheckRollup
gh pr merge <n> --repo owner/name --squash --delete-branch --match-head-commit <head-sha>
```

After a merge lands, re-check the rest and re-run `update-branch` on the ones that
went BEHIND again. Auto-merge does not update a stale branch for you; it waits.
Do not read `mergeStateStatus: UNKNOWN` as a failure — GitHub recomputes
mergeability asynchronously, so poll rather than concluding the merge is blocked.

### Stale CHANGES_REQUESTED

`dismiss_stale_reviews` only clears **approvals** on a new push. A
`CHANGES_REQUESTED` review keeps blocking merge even after the feedback is fully
addressed, and even where `required_approving_review_count` is 0.

Dismissal is legitimate ONLY when all three hold:

1. The blocker was actually fixed in code — not argued away.
2. A comment on the PR states what changed, with evidence (test output, live run).
3. The dismissal message says so and invites re-review.

```bash
gh api -X PUT repos/<owner>/<repo>/pulls/<n>/reviews/<review_id>/dismissals \
  -f message="Fixed: <what changed + evidence>. Dismissing this stale change-request; re-review welcome." \
  -f event=DISMISS
```

Dismiss your OWN stale reviews freely. Dismissing another member's review on their
PR needs the author's agreement first — say what you did and why in the thread.
Never dismiss to unblock a merge you simply disagree with.

## Security Incidents

For exposed secrets, OAuth client secrets, deployment tokens, or suspected credentials:

1. Do not repeat the secret in chat, issues, commit messages, or logs.
2. Create a security/governance issue in the relevant HypeProof repo or harness.
3. Rotate or revoke the credential in the provider console; this is external and must be marked blocked until done.
4. Remove the secret from current files and add scanning/prevention tests.
5. Verify public history exposure. If history purge is needed, track it separately and avoid destructive rewrites without explicit approval.
6. Keep private/public visibility decisions tied to the security issue until verification passes.

For Google OAuth exposure, rotation in Google Cloud Console is required; repo edits alone are not enough.

## Deploy And Vercel

When Vercel or deploy behavior is involved:

- Identify the deploy source: repo, branch, project, commit SHA, and actor.
- Confirm whether the deploy should happen on PR, merge to default branch, release tag, or manual workflow.
- If Vercel says a GitHub user is not a team member, distinguish between Vercel team membership, GitHub collaborator permission, and repo visibility.
- For public repos, Vercel can often deploy from GitHub without adding every contributor to a paid Vercel team; verify the actual integration.
- After merge, monitor the deployment URL until success or a concrete failure is found.

## Product Docs

For product docs consistency across `hypeproof-studio`, `hypeprooflab`, and `sediment`:

- Check both repo docs and live URLs when the user mentions broken links or deployed docs.
- Keep design and navigation consistent across products.
- Fix links directly and run a link/build check when available.
- Do not silently move product docs into personal or unrelated repos.

## Reporting

End with:

- What repo scope was used.
- What changed.
- What was verified.
- What remains blocked externally, such as approvals, pending invitations, provider console rotation, plan limits, or deploy propagation.

If you excluded a repo from scope, name it and explain that it is outside HypeProof-only operation.
