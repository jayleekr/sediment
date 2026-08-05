"""Single-flight for batch jobs — one runner per (job, tenant) at a time.

sediment#164.

There is no lock of any kind anywhere in this repository, yet the batch jobs
can start from several places at once: APScheduler's schedule, a launchd cron,
someone running `make distill` by hand, and a second scheduler process if one
is ever left running.

What that actually costs
------------------------
Not corruption. The issue originally claimed duplicate distill would create
fake contradictions via #141's sibling rule, and that is wrong: siblings are
minted only when the SOURCE differs, and `src` is deterministic
(`conv/<id>`, `discord/<channel>/<day>`). Two runs of the same job see the same
source and the "same source → reuse ref" rule collapses them onto one page.

The real cost is waste — the same transcript extracted twice by the LLM, the
same chunks re-embedded, the same rows rewritten. That is money and churn, not
damage.

That distinction drives two decisions here:

**Fail-open.** If acquiring the lock fails for any reason, the job runs.
A broken lock must never be able to stop knowledge from accumulating; paying
for one duplicate run is strictly cheaper than silently pausing ingestion.

**Transaction-scoped, not session-scoped.** `lab_lib/settings.py` records that
prod points at the Supabase pooler, and CLAUDE.md lists "pool-level (PgBouncer
transaction mode mismatch)" as a known trap. Under transaction pooling a
connection is not pinned to you between statements, so a session-level
`pg_advisory_lock` can be taken on one backend and released against another —
it protects nothing. `pg_advisory_xact_lock` is bound to the transaction, and
transaction pooling pins the connection for exactly that long.

The cost of that choice is stated plainly: the lock is held by keeping one
transaction open for the job's duration, which parks a connection in
`idle in transaction` and holds a snapshot that blocks vacuum from reclaiming
rows newer than it. Acceptable here because the guarded jobs run for minutes,
only one holds the lock at a time, and the alternative — a session lock behind
a transaction pooler — is not a lock at all.
"""
from __future__ import annotations

import contextlib
from typing import AsyncIterator, Optional

from sqlalchemy import text

from .db import SessionService
from .logging import get_logger

log = get_logger("joblock")

#: Jobs worth guarding: they call an LLM, re-embed, or rewrite artifacts.
#: Read-only or cheap jobs are deliberately absent — a lock costs a pinned
#: connection, which is only worth paying where a duplicate run costs more.
GUARDED_JOBS = (
    "distill",
    "consolidate",
    "discord_fetch",
    "github_repo_sync",
    "judge_daily",
    "retention_sweep",
)


def lock_key(job: str, tenant_id: Optional[str] = None) -> str:
    """Namespaced key. Tenant-scoped so one tenant's long run cannot block
    another's — the jobs are per-tenant work, not global maintenance."""
    return f"sediment:{job}:{tenant_id or 'global'}"


@contextlib.asynccontextmanager
async def job_lock(job: str, tenant_id: Optional[str] = None) -> AsyncIterator[bool]:
    """Hold single-flight for `job` while the block runs.

    Yields True when this process owns the run, False when another holder was
    already running — the caller should skip, and skipping is NOT an error.

    Yields True on any acquisition failure (fail-open, see module docstring),
    logging a warning so the degraded state is visible rather than assumed.

    Usage::

        async with job_lock("distill", tenant_id) as acquired:
            if not acquired:
                return
            ...
    """
    session = None
    acquired = False
    try:
        session = SessionService()
        # hashtext() maps the namespaced key onto the int8 advisory-lock space.
        # Collisions across different keys are possible in principle; the blast
        # radius is one job skipping a tick, which the next tick undoes.
        r = await session.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
            {"key": lock_key(job, tenant_id)},
        )
        acquired = bool(r.scalar())
    except Exception as e:
        log.warning("joblock.acquire_failed_running_anyway",
                    job=job, tenant=tenant_id, err=str(e)[:200])
        if session is not None:
            with contextlib.suppress(Exception):
                await session.rollback()
                await session.close()
        # Fail-open: a broken lock must not stop the job.
        yield True
        return

    if not acquired:
        log.info("joblock.busy_skipping", job=job, tenant=tenant_id)
        with contextlib.suppress(Exception):
            await session.rollback()
            await session.close()
        yield False
        return

    log.info("joblock.acquired", job=job, tenant=tenant_id)
    try:
        yield True
    finally:
        # Committing ends the transaction, which is what releases the lock.
        # Rollback would release it too; commit is used so the holder does not
        # look like a failure in Postgres' stats.
        with contextlib.suppress(Exception):
            await session.commit()
        with contextlib.suppress(Exception):
            await session.close()
        log.info("joblock.released", job=job, tenant=tenant_id)
