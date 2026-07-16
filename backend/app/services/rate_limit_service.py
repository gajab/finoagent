"""Process-wide guard for outbound yfinance traffic.

Three layers, so that no matter how many users load a 300-holding portfolio at
once (or hammer "Refresh All"), we never bombard Yahoo into a per-IP block:

  1. Token bucket   — caps the sustained request *rate* (with a small burst).
  2. Semaphore      — caps how many batch jobs run *concurrently*.
  3. Circuit breaker — after repeated rate-limit signals, trips OPEN and makes
                       callers serve cache-only for a cooldown, then half-opens.

The token bucket and semaphore are asyncio primitives touched only from the
event loop. The breaker holds plain ints/timestamps so the blocking fetchers
(running in a worker thread via asyncio.to_thread) can safely report failures
back into it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

# ── Tunables (sane defaults; conservative enough for a single shared IP) ──────
_RATE_PER_SEC = 4.0       # sustained ~240 requests/min across the whole process
_BURST = 8.0              # allow short bursts up to this many tokens
_MAX_CONCURRENCY = 4      # simultaneous batch jobs hitting yfinance
_FAIL_THRESHOLD = 3       # consecutive rate-limit signals before tripping
_COOLDOWN_SECONDS = 60.0  # how long the breaker stays open


class YFinanceBlocked(Exception):
    """Raised by yf_guard() when the breaker is open — callers serve cache-only."""


class _TokenBucket:
    def __init__(self, rate_per_sec: float, capacity: float):
        self.rate = rate_per_sec
        self.capacity = capacity
        self.tokens = capacity
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, cost: float = 1.0) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= cost:
                    self.tokens -= cost
                    return
                wait = (cost - self.tokens) / self.rate
            await asyncio.sleep(min(wait, 5.0))


class _Breaker:
    def __init__(self, fail_threshold: int, cooldown: float):
        self.fail_threshold = fail_threshold
        self.cooldown = cooldown
        self.failures = 0
        self.open_until = 0.0

    def is_open(self) -> bool:
        return time.monotonic() < self.open_until

    def record_success(self) -> None:
        self.failures = 0

    def trip(self) -> None:
        self.failures += 1
        if self.failures >= self.fail_threshold:
            self.open_until = time.monotonic() + self.cooldown
            self.failures = 0
            logger.warning("yfinance circuit breaker OPEN for %.0fs (rate-limit signals)", self.cooldown)


_bucket = _TokenBucket(_RATE_PER_SEC, _BURST)
_sem = asyncio.Semaphore(_MAX_CONCURRENCY)
_breaker = _Breaker(_FAIL_THRESHOLD, _COOLDOWN_SECONDS)

_RATE_LIMIT_MARKERS = (
    "429", "too many requests", "rate limit", "rate-limit", "ratelimited",
    "unavailable for legal reasons", "temporarily blocked",
)


def looks_rate_limited(exc: object) -> bool:
    s = str(exc).lower()
    return any(m in s for m in _RATE_LIMIT_MARKERS)


def note_failure(exc: object) -> None:
    """Report a fetch failure; trips the breaker only on rate-limit-like signals.

    Safe to call from a worker thread (mutates plain attributes only).
    """
    if looks_rate_limited(exc):
        _breaker.trip()


def note_success() -> None:
    """Report a successful fetch (resets the consecutive-failure counter)."""
    _breaker.record_success()


def breaker_open() -> bool:
    return _breaker.is_open()


@asynccontextmanager
async def yf_guard(cost: float = 1.0):
    """Acquire a paced, concurrency-bounded slot for a yfinance batch call.

    Raises YFinanceBlocked immediately if the breaker is open so the caller can
    fall back to cache-only instead of piling onto an already-throttled IP.
    """
    if _breaker.is_open():
        raise YFinanceBlocked("yfinance circuit breaker is open")
    async with _sem:
        await _bucket.acquire(cost)
        yield


# ── Test / ops helpers ────────────────────────────────────────────────────────
def _reset_for_tests() -> None:
    global _bucket, _sem, _breaker
    _bucket = _TokenBucket(_RATE_PER_SEC, _BURST)
    _sem = asyncio.Semaphore(_MAX_CONCURRENCY)
    _breaker = _Breaker(_FAIL_THRESHOLD, _COOLDOWN_SECONDS)
