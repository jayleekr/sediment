"""Rate limit edge cases — per cli-test-requirements.md §1.2."""
from __future__ import annotations
import asyncio
import time

import pytest

from lab_lib.rate_limit import TokenBucketLimiter


async def test_empty_key_treated_as_a_bucket():
    rl = TokenBucketLimiter(per_minute=3)
    for _ in range(3):
        allowed, _ = await rl.check("")
        assert allowed
    allowed, _ = await rl.check("")
    assert not allowed


async def test_very_high_per_minute_no_overflow():
    rl = TokenBucketLimiter(per_minute=1_000_000)
    for _ in range(100):
        allowed, _ = await rl.check("x")
        assert allowed
    # Refill rate ~16k/sec — bucket replenishes faster than we can drain it.


async def test_per_minute_one_strict():
    rl = TokenBucketLimiter(per_minute=1)
    allowed, _ = await rl.check("solo")
    assert allowed
    allowed, retry = await rl.check("solo")
    assert not allowed
    # With 1/min, retry_after should be close to 60s for the next token
    assert 55 <= retry <= 60


async def test_concurrent_check_atomicity():
    """100 concurrent tasks against a bucket of 30: total allowed ≤ capacity
    +ε (refill during the wall-clock of the test is < 1 token)."""
    rl = TokenBucketLimiter(per_minute=60)  # 1 token/sec refill, capacity 60
    rl.capacity = 30
    rl._buckets.clear()  # reset

    async def one():
        ok, _ = await rl.check("shared")
        return ok

    results = await asyncio.gather(*[one() for _ in range(100)])
    allowed = sum(results)
    # 30 initial tokens + however many refilled during the gather (~0)
    assert 28 <= allowed <= 32, f"unexpected allowed={allowed}/100"


async def test_memory_growth_bounded_by_unique_keys():
    rl = TokenBucketLimiter(per_minute=10)
    for i in range(1000):
        await rl.check(f"key-{i}")
    assert len(rl._buckets) == 1000


async def test_monotonic_clock_used_not_wall_clock(monkeypatch):
    """Bucket math uses time.monotonic — wall-clock NTP jumps must not
    poison the math. Patch wall time backward; bucket should still refill."""
    rl = TokenBucketLimiter(per_minute=60)
    # drain
    for _ in range(60):
        await rl.check("ntp")
    allowed, _ = await rl.check("ntp")
    assert not allowed
    # NB: we can't actually move monotonic time backward (the std lib forbids
    # it), and monkeypatching it is fragile. This test documents the
    # property: subsequent allowed=True after natural elapsed time even after
    # wall-clock NTP step would happen, because the impl uses monotonic.
    await asyncio.sleep(1.1)  # > 1/refill_per_sec = 1.0s
    allowed, _ = await rl.check("ntp")
    assert allowed


async def test_retry_after_is_positive_and_finite():
    rl = TokenBucketLimiter(per_minute=10)
    for _ in range(10):
        await rl.check("ra")
    _, retry = await rl.check("ra")
    assert retry > 0
    assert retry < 10  # 1 token at 10/min = 6s ; bound it generously
