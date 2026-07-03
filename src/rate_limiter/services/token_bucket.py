"""FR2: Token bucket algorithm — burst-tolerant rate limiting.

Core algorithm: tracks (tokens, last_refill) per key. Refill formula:
    tokens = min(burst, tokens + elapsed * rate)
If tokens >= cost, deduct and allow. Else deny.

Uses time.monotonic() for refill computation (immune to wall-clock jumps).
Thread-safe per-bucket via asyncio.Lock.
Periodic lock cleanup for idle buckets.

Tier: staff-engineer — correctness-critical: atomicity, lock lifecycle, refill math.
"""

import asyncio
import time
from collections import defaultdict

from src.rate_limiter.config import settings


class TokenBucketService:
    """In-memory token bucket rate limiter.

    Each bucket stores (tokens, last_refill) keyed by composite client key.
    Refill is computed on every check() call using elapsed monotonic time.
    Per-bucket asyncio.Lock prevents double-spend on concurrent requests.
    """

    def __init__(self):
        # Bucket state: key -> {"tokens": float, "last_refill": float}
        self._buckets: dict[str, dict[str, float]] = {}
        # Per-bucket locks for atomic refill-check-deduct
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        # Track last access time for lock cleanup
        self._last_access: dict[str, float] = {}
        # Background cleanup task
        self._cleanup_task: asyncio.Task | None = None

    async def start_cleanup(self) -> None:
        """Start periodic lock cleanup (call from lifespan startup)."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    def clear(self) -> None:
        """Drop all bucket state and locks (test isolation)."""
        self._buckets.clear()
        self._locks.clear()
        self._last_access.clear()

    async def _cleanup_loop(self) -> None:
        """Periodically remove locks for idle buckets."""
        while True:
            await asyncio.sleep(settings.lock_cleanup_interval_sec)
            now = time.monotonic()
            idle_keys = [
                k for k, t in self._last_access.items() if now - t > settings.bucket_idle_ttl_sec
            ]
            for k in idle_keys:
                self._locks.pop(k, None)
                self._buckets.pop(k, None)
                self._last_access.pop(k, None)

    async def check(
        self,
        key: str,
        rate: float,
        burst: float,
        cost: float = 1.0,
    ) -> tuple[bool, int, int]:
        """Atomically refill, check, and deduct tokens.

        Args:
            key: Composite bucket key ("{client_type}:{client_value}:{rule_id}")
            rate: Tokens per second (limit / window_sec)
            burst: Maximum token capacity
            cost: Tokens to consume (default 1; use >1 for weighted requests)

        Returns:
            (allowed, remaining, reset_at) — reset_at is Unix timestamp of next token arrival.
        """
        lock = self._locks[key]

        async with lock:
            now_mono = time.monotonic()
            self._last_access[key] = now_mono

            bucket = self._buckets.get(key)
            if bucket is None:
                # First request — initialize at full burst
                bucket = {"tokens": burst, "last_refill": now_mono}
                self._buckets[key] = bucket

            # Refill — credit only whole tokens to prevent micro-refills
            # (e.g. 7 ms at 100 tok/s should not yield a token). The integer
            # elapsed*rate floors fractional token accumulation.
            elapsed = now_mono - bucket["last_refill"]
            refill = int(elapsed * rate)
            bucket["tokens"] = min(burst, bucket["tokens"] + refill)
            bucket["last_refill"] = now_mono

            # Check + deduct
            if bucket["tokens"] >= cost:
                bucket["tokens"] -= cost
                allowed = True
            else:
                allowed = False

            remaining = int(bucket["tokens"])

            # Compute reset_at: when the next token arrives (or window resets)
            if allowed or bucket["tokens"] <= 0:
                # Time until enough tokens for 1 more request
                deficit = max(0, cost - bucket["tokens"])
                seconds_until_next = deficit / rate if rate > 0 else settings.default_window_sec
                reset_at = int(time.time() + seconds_until_next)
            else:
                reset_at = int(time.time())

            return allowed, remaining, reset_at
