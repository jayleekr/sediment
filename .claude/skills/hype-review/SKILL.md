---
name: hype-review
description: Check PR review requests and guide HypeProof PR reviews. Use this whenever the user asks to review a PR, check reviews assigned to them, handle a GitHub review request, decide approve/comment/request changes, or draft a response to a PR author. Automatically infer the reviewer and default lenses from policy; do not require manual --role unless the user explicitly asks for an extra lens.
user_invocable: true
triggers:
  - "hype-review"
  - "review request"
  - "pr review"
  - "리뷰 요청"
  - "리뷰해"
  - "리뷰 확인"
  - "내 리뷰"
  - "PR 리뷰"
argument_hint: "[PR URL | PR number | --mine] — optional"
---

# hype-review

Use this skill to help a HypeProof member handle PR review work. The goal is
not to auto-approve. The goal is to make the reviewer think through the PR
from their default team lens, ask useful questions, and leave an actionable
reply for the PR author.

The deterministic engine is:

```bash
python3 scripts/hype-review/review.py
```

Run it from the repo root. It reads `policy/members.yaml` when present and has
an embedded fallback lens map for vendored product repos.

Maintainers can audit or apply all-member reviewer requests with:

```bash
python3 scripts/hype-review/request_reviewers.py
python3 scripts/hype-review/request_reviewers.py --apply
```

---

## Default Flow

1. Find the repo root.

   ```bash
   git rev-parse --show-toplevel
   ```

   If this fails, ask the user for a repo or PR URL.

2. Confirm the script exists.

   ```bash
   test -f scripts/hype-review/review.py
   ```

   If it is missing, tell the user this repo needs the latest harness sync.

3. Infer the reviewer from GitHub CLI.

   ```bash
   gh api user --jq .login
   ```

   If `gh` is not authenticated, ask the user for their GitHub login and pass
   it with `--reviewer <login>`.

4. Choose mode.

   - If the user asks for "my reviews", "내 리뷰", or gives no specific PR:

     ```bash
     python3 scripts/hype-review/review.py --mine
     ```

   - If the user gives a PR URL:

     ```bash
     python3 scripts/hype-review/review.py --repo <owner/name> --pr <number> --reviewer <login>
     ```

   - If the user gives only a PR number, infer the repo:

     ```bash
     gh repo view --json nameWithOwner --jq .nameWithOwner
     python3 scripts/hype-review/review.py --repo <owner/name> --pr <number> --reviewer <login>
     ```

5. Do not pass `--role` by default.

   The script already maps reviewers to default lenses:

   | Reviewer | Default lenses |
   |---|---|
   | `jayleekr` | `maintainer`, `product`, `security`, `deploy`, `docs` |
   | `JeHyeong2` | `maintainer`, `backend`, `security` |
   | `ico1036` | `contributor`, `docs` |
   | `xoqhdgh1002` | `contributor`, `product` |
   | `JinyongShin` | `backend`, `deploy` |
   | `TJ-kr` | `frontend`, `product` |

   Add `--role <lens>` only when the user explicitly asks to inspect an extra
   angle for this PR.

6. If the user asks to assign reviews to the team, run reviewer request audit
   first:

   ```bash
   python3 scripts/hype-review/request_reviewers.py --repo <owner/name>
   ```

   Only use `--apply` when the intent is to mutate GitHub reviewer requests.
   The script skips the PR author, already-requested members, and members who
   already left a review. If GitHub cannot request a member because an
   invitation is pending, leave a fallback mention comment on the PR.

---

## Review Behavior

When showing results to the user:

- Lead with the PR snapshot and the role/risk questions.
- Do not paste the entire worksheet if it is too long; summarize the important
  questions and tell the user where the full output is available.
- Keep the distinction clear:
  - review requests go to all active members for learning and coverage
  - merge waits for branch protection, CODEOWNERS, and required checks
  - security/deploy/governance blockers must be resolved even if quorum passes
- End with an author reply draft using one of:
  - `approve`
  - `comment`
  - `request_changes`

For blocker feedback, always include:

```markdown
Blocker: <file/line or behavior>
Risk: <security/data/deploy/user impact>
Expected fix: <specific change>
Re-test: <verification command or scenario>
```

---

## Examples

Check the current user's review queue:

```bash
python3 scripts/hype-review/review.py --mine
```

Review a specific PR using the reviewer's default lenses:

```bash
python3 scripts/hype-review/review.py --repo jayleekr/hypeproof-harness --pr 28 --reviewer TJ-kr
```

Add an extra one-off lens:

```bash
python3 scripts/hype-review/review.py --repo jayleekr/sediment --pr 87 --reviewer TJ-kr --role security
```
