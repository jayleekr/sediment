# AGENTS.md - Sediment Agent Operating Rules

This is the canonical instruction file for coding agents working in this
repository. Keep it short and operational. Tool-specific files such as
`CLAUDE.md` may add adapter details, but should not duplicate these rules.

## Close-Out Is Not Done Until There Is a PR

When an agent finishes implementation or production validation, it must create
a focused pull request before saying the session can be closed.

Required close-out steps:

1. Separate task changes from unrelated dirty worktree changes.
2. Commit only the task-relevant files on a branch.
3. Push the branch and open a PR against `main`.
4. Put test results, deploy version, smoke output, and known follow-ups in the
   PR body.
5. Update linked GitHub issues with the same operational facts.

Do not direct-push to `main`. `main` must stay protected, including force-push
and deletion protection. If protection is missing and you have admin
permissions, enable it before close-out.

## Dirty Worktree Discipline

Assume unrelated dirty files belong to the user or another task. Do not revert
or commit them unless explicitly asked. For production deploys, build from a
clean tree or from a committed branch and copy in only the files that belong to
the current task.

## Production Validation

For Sediment changes that affect auth, retrieval, tenant routing, ingestion, or
deployment configuration, include both local validation and a real production
smoke when feasible. Record the Fly image/version and the exact tenant/query
used for smoke validation.

## Useful Commands

```bash
cd services/sediment
PYTHONPATH=. .venv/bin/pytest tests/test_data_quality_guards.py tests/test_oauth_exchange.py tests/test_smoke_conv_title.py tests/test_github_repo_fetch_meta.py -q
.venv/bin/ruff check applications/sediment_platform/routers/library.py tests/test_data_quality_guards.py
```

Use `bash harness/scripts/fly-exec.sh "<cmd>"` for non-interactive Fly
commands. Avoid `fly ssh console -C` for automation; it is prone to hanging.
