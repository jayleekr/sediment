# SOUL.md - Sediment Operating Memory

Sediment is an evidence-grounded memory layer. Agent work should leave the
system more traceable than it found it: code, tests, deployment facts, and issue
comments must line up.

## Operating Principle

A session is not complete just because production appears healthy. It is
complete when the change is reviewable, reproducible, and recoverable:

- a PR exists,
- `main` is protected from direct mutation,
- validation evidence is written down,
- linked issues describe what changed and what remains,
- unrelated local changes are not mixed into the task.

## Main Branch

`main` is the shared record. Keep branch protection enabled. Use pull requests
for operational fixes, emergency patches, and agent-generated work. Do not make
direct pushes the default path.

## Deployment Memory

If a production deploy was needed, record the deployed image or machine version
in the PR and issue comments. If a resource change was needed, such as Fly
memory scaling, record the old value, new value, and observed reason.
