"""In-process token bucket rate limit, keyed by member_id.

v1 scope: one bucket per process. Multi-process deployments will need
Redis (sliding window with INCR + EXPIRE). The settings knob
`query_ratelimit_per_min` already exists — we honor it here.

Bucket size = `query_ratelimit_per_min`, refill = 1 token per (60/limit) seconds.
"""
from __future__ import annotations
import asyncio
import time
from dataclasses import dataclass

from .settings import settings


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


class TokenBucketLimiter:
    def __init__(self, per_minute: int | None = None):
        self.capacity = float(per_minute or settings.query_ratelimit_per_min)
        self.refill_per_sec = self.capacity / 60.0
        self._buckets: dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> tuple[bool, float]:
        """Return (allowed, retry_after_seconds). Idempotent: a denied call
        does NOT consume a token (so a burst doesn't worsen its own wait)."""
        now = time.monotonic()
        async with self._lock:
            b = self._buckets.get(key)
            if b is None:
                b = _Bucket(tokens=self.capacity, last_refill=now)
                self._buckets[key] = b
            elapsed = now - b.last_refill
            b.tokens = min(self.capacity, b.tokens + elapsed * self.refill_per_sec)
            b.last_refill = now
            if b.tokens >= 1.0:
                b.tokens -= 1.0
                return True, 0.0
            # Tokens needed to refill to 1
            deficit = 1.0 - b.tokens
            retry_after = deficit / self.refill_per_sec
            return False, retry_after


_default_limiter: TokenBucketLimiter | None = None


def default_limiter() -> TokenBucketLimiter:
    global _default_limiter
    if _default_limiter is None:
        _default_limiter = TokenBucketLimiter()
    return _default_limiter
