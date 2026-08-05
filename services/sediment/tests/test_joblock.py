"""sediment#164 — single-flight for batch jobs.

There is no lock of any kind anywhere in this repository, yet the batch jobs
can start from several places at once: APScheduler, a launchd cron, someone
running `make distill`, and a second scheduler process.

The issue claimed duplicate distill would create fake contradictions via #141's
sibling rule. Verified before building: wrong. Siblings are minted only when the
SOURCE differs, and `src` is deterministic, so two runs of the same job land on
the "same source → reuse ref" branch. The real cost is waste — the same
transcript extracted twice by the LLM, the same chunks re-embedded.

That distinction is what these tests actually pin: fail-open (a broken lock must
never stop ingestion, because paying for a duplicate run is cheaper than
silently pausing it) and transaction-scoped (prod runs behind the Supabase
pooler, where a session-level lock is not a lock).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from lab_lib.joblock import GUARDED_JOBS, job_lock, lock_key

REPO = Path(__file__).resolve().parents[3]
SVC = REPO / "services" / "sediment"


def _read(rel: str) -> str:
    return (SVC / rel).read_text()


# ---------------------------------------------------------------------------
# Key namespacing
# ---------------------------------------------------------------------------

def test_key_is_namespaced_and_tenant_scoped():
    """Tenant-scoped so one tenant's long run cannot block another's — these
    are per-tenant workloads, not global maintenance."""
    assert lock_key("distill", "t1") == "sediment:distill:t1"
    assert lock_key("distill", "t2") != lock_key("distill", "t1")
    assert lock_key("distill") == "sediment:distill:global"
    assert lock_key("consolidate", "t1") != lock_key("distill", "t1")


# ---------------------------------------------------------------------------
# Acquire / skip / release
# ---------------------------------------------------------------------------

class _Result:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _Session:
    def __init__(self, value=True, raise_on_execute=None):
        self.value = value
        self.raise_on_execute = raise_on_execute
        self.sql: list[str] = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    async def execute(self, stmt, params=None):
        self.sql.append(str(stmt))
        self.params = params
        if self.raise_on_execute:
            raise self.raise_on_execute
        return _Result(self.value)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def close(self):
        self.closed = True


def _with_session(sess):
    return patch("lab_lib.joblock.SessionService", lambda: sess)


async def test_acquired_lock_yields_true_and_releases_by_committing():
    """The transaction IS the lock — ending it is what releases."""
    sess = _Session(value=True)
    with _with_session(sess):
        async with job_lock("distill", "t1") as acquired:
            assert acquired is True
            assert not sess.committed, "lock must still be held inside the block"
    assert sess.committed, "commit ends the transaction and releases the lock"
    assert sess.closed


async def test_busy_lock_yields_false_and_does_not_hold_a_connection():
    sess = _Session(value=False)
    with _with_session(sess):
        async with job_lock("distill", "t1") as acquired:
            assert acquired is False
    assert sess.rolled_back, "a lock we do not hold must not keep a transaction open"
    assert sess.closed


async def test_it_uses_the_transaction_scoped_lock_not_the_session_one():
    """Prod points at the Supabase pooler (see lab_lib/settings.py). Under
    transaction pooling the connection is not pinned between statements, so
    pg_advisory_lock can be taken on one backend and released against another —
    it protects nothing. pg_advisory_xact_lock is bound to the transaction,
    which IS pinned."""
    sess = _Session(value=True)
    with _with_session(sess):
        async with job_lock("distill", "t1"):
            pass
    sql = " ".join(sess.sql)
    assert "pg_try_advisory_xact_lock" in sql
    assert "pg_advisory_lock(" not in sql
    assert "pg_try_advisory_lock(" not in sql


async def test_the_key_is_bound_not_interpolated():
    sess = _Session(value=True)
    with _with_session(sess):
        async with job_lock("distill", "t1"):
            pass
    assert sess.params == {"key": "sediment:distill:t1"}


# ---------------------------------------------------------------------------
# Fail-open — the property that follows from the harm being waste
# ---------------------------------------------------------------------------

async def test_acquisition_failure_runs_the_job_anyway():
    """A broken lock must never stop knowledge from accumulating. One duplicate
    run costs LLM tokens; a silently paused pipeline costs the product."""
    sess = _Session(raise_on_execute=RuntimeError("pooler unreachable"))
    with _with_session(sess):
        async with job_lock("distill", "t1") as acquired:
            assert acquired is True, "must fail OPEN, not closed"


async def test_session_construction_failure_also_fails_open():
    def _boom():
        raise RuntimeError("no engine")

    with patch("lab_lib.joblock.SessionService", _boom):
        async with job_lock("distill", "t1") as acquired:
            assert acquired is True


async def test_release_survives_a_failing_commit():
    """A job that finished must not surface a lock-teardown error as its own
    failure."""
    sess = _Session(value=True)

    async def _bad_commit():
        raise RuntimeError("connection reset")

    sess.commit = _bad_commit
    with _with_session(sess):
        async with job_lock("distill", "t1") as acquired:
            assert acquired is True
    assert sess.closed


async def test_an_exception_inside_the_block_still_releases():
    sess = _Session(value=True)
    with _with_session(sess), pytest.raises(ValueError):
        async with job_lock("distill", "t1"):
            raise ValueError("job blew up")
    assert sess.committed, "the lock must not outlive a crashed job"


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("job", GUARDED_JOBS)
def test_every_guarded_job_actually_takes_the_lock(job):
    src = _read("scripts/scheduler.py")
    assert f'job_lock("{job}"' in src, f"{job} is listed as guarded but never locks"


def test_skipping_is_a_return_not_an_error():
    """Losing the race is the normal case — another runner is already doing the
    work. Raising would turn a healthy outcome into alert noise."""
    src = _read("scripts/scheduler.py")
    assert src.count("if not acquired:\n            return") == len(GUARDED_JOBS)


def test_the_discord_sweep_is_guarded_as_a_whole_not_per_channel():
    """Two overlapping sweeps guarded per channel would still interleave and
    duplicate every Discord API call the lock exists to avoid."""
    src = _read("scripts/scheduler.py")
    assert 'job_lock("discord_fetch")' in src
    assert "async def _fetch_targets(" in src


def test_cheap_jobs_are_deliberately_unguarded():
    """A lock parks a connection in `idle in transaction` for the job's
    duration. That is only worth paying where a duplicate run costs more —
    an LLM call, a re-embed, or an artifact rewrite."""
    for cheap in ("signal_derivation", "hard_negative_mining", "cost_monitor"):
        assert cheap not in GUARDED_JOBS


def test_the_pooler_tradeoff_is_written_down():
    """The cost of a transaction-scoped lock — a pinned connection and a held
    snapshot — must be stated where the next person will find it, not
    discovered from a vacuum alert."""
    src = _read("lab_lib/joblock.py")
    assert "idle in transaction" in src
    assert "vacuum" in src
    assert "pooler" in src.lower()
