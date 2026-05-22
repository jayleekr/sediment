"""Unit tests for the token-bucket rate limiter — no DB."""
from __future__ import annotations
import asyncio

import pytest

from lab_lib.rate_limit import TokenBucketLimiter


async def test_first_n_calls_allowed():
    rl = TokenBucketLimiter(per_minute=60)
    for _ in range(60):
        allowed, _ = await rl.check("alice")
        assert allowed


async def test_burst_then_denied():
    rl = TokenBucketLimiter(per_minute=10)
    for _ in range(10):
        allowed, _ = await rl.check("bob")
        assert allowed
    allowed, retry = await rl.check("bob")
    assert not allowed
    assert retry > 0


async def test_denied_does_not_consume_more_tokens():
    rl = TokenBucketLimiter(per_minute=10)
    for _ in range(10):
        await rl.check("carol")
    # Two consecutive denies should report similar (or shorter) retry_after.
    _, r1 = await rl.check("carol")
    _, r2 = await rl.check("carol")
    assert r2 <= r1 + 0.05, "deny should not consume — wait should not grow"


async def test_separate_keys_independent():
    rl = TokenBucketLimiter(per_minute=2)
    a1, _ = await rl.check("a"); a2, _ = await rl.check("a")
    assert a1 and a2
    a3, _ = await rl.check("a")
    assert not a3
    # bob is fresh
    b1, _ = await rl.check("b")
    assert b1


async def test_refill_after_wait():
    rl = TokenBucketLimiter(per_minute=120)  # 2 tokens / second
    # drain
    for _ in range(120):
        await rl.check("dave")
    # next is denied
    allowed, _ = await rl.check("dave")
    assert not allowed
    # wait for one token to refill (≈ 0.5s)
    await asyncio.sleep(0.6)
    allowed, _ = await rl.check("dave")
    assert allowed
