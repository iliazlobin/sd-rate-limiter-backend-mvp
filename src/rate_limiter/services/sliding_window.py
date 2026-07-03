"""FR4: Sliding window counter — near-exact rate limiting with O(1) memory.

Uses Cloudflare's weighted-estimate formula:
    window_id   = floor(now / window_sec)
    elapsed     = now % window_sec
    weight      = (window_sec - elapsed) / window_sec
    estimated   = prev_count * weight + curr_count

If estimated >= limit, reject. Else increment curr_count and allow.

Measured 0.003% error rate across 400M requests from 270K sources (Cloudflare, 2017).
~6% average drift acceptable for abuse detection.

Tier: staff-engineer — correctness-critical: window arithmetic, edge cases, atomicity.
"""

import asyncio
import time
from collections import defaultdict

from src.rate_limiter.config import settings


class SlidingWindowService:
    """In-memory sliding window counter rate limiter.

    Each bucket stores (prev_count, curr_count, curr_window).
    On window rollover: prev_count = curr_count, curr_count = 1, curr_window = new_id.
    Per-bucket asyncio.Lock prevents races on concurrent requests.
    """

    def __init__(self):
        # Bucket state: key -> {"prev_count": int, "curr_count": int, "curr_window": int}
        self._buckets: dict[str, dict[str, int]] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last_access: dict[str, float] = {}
        self._cleanup_task: asyncio.Task | None = None

    async def start_cleanup(self) -> None:
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    def clear(self) -> None:
        """Drop all bucket state and locks (test isolation)."""
        self._buckets.clear()
        self._locks.clear()
        self._last_access.clear()

    async def _cleanup_loop(self) -> None:
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
        limit: int,
        window_sec: int,
    ) -> tuple[bool, int, int]:
        """Atomically check and increment the sliding window counter.

        Args:
            key: Composite bucket key
            limit: Max requests allowed in the window
            window_sec: Window size in seconds

        Returns:
            (allowed, remaining, reset_at) — reset_at is Unix timestamp of window end.
        """
        lock = self._locks[key]

        async with lock:
            now = time.time()
            now_mono = time.monotonic()
            self._last_access[key] = now_mono

            window_id = int(now // window_sec)
            elapsed_in_window = now % window_sec
            weight = (window_sec - elapsed_in_window) / window_sec

            bucket = self._buckets.get(key)

            if bucket is None:
                # First request for this key
                bucket = {
                    "prev_count": 0,
                    "curr_count": 1,
                    "curr_window": window_id,
                }
                self._buckets[key] = bucket
                reset_at = int((window_id + 1) * window_sec)
                return True, limit - 1, reset_at

            # Window rollover detection
            if window_id > bucket["curr_window"]:
                # New window — carry forward
                if window_id == bucket["curr_window"] + 1:
                    bucket["prev_count"] = bucket["curr_count"]
                else:
                    # Gap (clock jump or long idle) — reset both windows
                    bucket["prev_count"] = 0
                bucket["curr_count"] = 1
                bucket["curr_window"] = window_id
                self._buckets[key] = bucket
                reset_at = int((window_id + 1) * window_sec)
                return True, limit - 1, reset_at

            if window_id < bucket["curr_window"]:
                # Clock jumped backward (NTP correction) — treat as first request
                bucket["prev_count"] = 0
                bucket["curr_count"] = 1
                bucket["curr_window"] = window_id
                self._buckets[key] = bucket
                reset_at = int((window_id + 1) * window_sec)
                return True, limit - 1, reset_at

            # Same window — compute weighted estimate
            estimated = bucket["prev_count"] * weight + bucket["curr_count"]

            if estimated >= limit:
                # Denied — do NOT increment curr_count
                remaining = max(0, limit - int(estimated))
                reset_at = int((window_id + 1) * window_sec)
                return False, remaining, reset_at

            # Allowed — increment
            bucket["curr_count"] += 1
            self._buckets[key] = bucket

            estimated_after = bucket["prev_count"] * weight + bucket["curr_count"]
            remaining = max(0, limit - int(estimated_after))
            reset_at = int((window_id + 1) * window_sec)
            return True, remaining, reset_at
