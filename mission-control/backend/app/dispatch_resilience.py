"""Retry + circuit breaker for the OpenRouter LLM call.

Before this module: any transient OpenRouter hiccup (5xx, timeout, TLS
reset) was fatal — the user received a 502 straight from dispatch. With
resilience in place:

- **Retry**: one retry on retryable errors (5xx, timeout, connection),
  with a short backoff + jitter. 4xx errors are NOT retried (they are
  client errors and won't self-heal). 429 is an exception — rate limits
  are worth one retry.

- **Circuit breaker**: after N consecutive failures in a rolling window,
  the breaker opens. While open, calls short-circuit with
  ``CircuitBreakerOpen`` so dispatch can emit a canned L4 degradation
  reply in milliseconds instead of waiting for another round of timeouts.
  A cooldown gates the transition to "half-open": the next call is
  allowed as a probe; success closes the breaker, failure re-opens it.

Design notes:

- Tiny, dependency-free. No tenacity, no pybreaker — five messages a day
  doesn't justify a framework.
- Breaker state is held in a module-level singleton (``openrouter_breaker``)
  because the OpenRouter outbound call happens from exactly one place.
  Tests ``reset()`` it between cases.
- ``snapshot()`` exposes state dict for events.jsonl logging.
- Timings use ``time.monotonic()`` so clock drift / NTP jumps don't
  confuse the breaker.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import random
import time
from typing import Any, Awaitable, Callable, Deque, Dict, Optional

from fastapi import HTTPException

logger = logging.getLogger("mission-control.dispatch_resilience")


class CircuitBreakerOpen(Exception):
    """Raised when the breaker is open and a call is short-circuited."""


# ── Classification ───────────────────────────────────────────────────────────

# 4xx client errors that should NOT be retried: the request itself is bad.
_NON_RETRYABLE_4XX = frozenset({400, 401, 403, 404, 422})


def is_openrouter_retryable(exc: BaseException) -> bool:
    """Classify an exception as retryable (transient) or not.

    Retryable: asyncio.TimeoutError, 5xx HTTPExceptions, 429 rate limits,
    network-level errors.
    """
    if isinstance(exc, asyncio.TimeoutError):
        return True
    if isinstance(exc, HTTPException):
        status = getattr(exc, "status_code", 500)
        if status in _NON_RETRYABLE_4XX:
            return False
        if 500 <= status <= 599:
            return True
        if status == 429:
            return True
        return False
    # Socket / connection errors are retryable.
    if isinstance(exc, (ConnectionError, OSError)):
        return True
    return False


# ── Retry ────────────────────────────────────────────────────────────────────

async def call_with_retry(
    fn: Callable[[], Awaitable[Any]],
    *,
    max_retries: int = 1,
    backoff_ms: int = 500,
    is_retryable: Callable[[BaseException], bool] = is_openrouter_retryable,
) -> Any:
    """Call an async function with bounded retries and jittered backoff.

    Only exceptions classified as retryable trigger a retry. Any other
    exception propagates immediately after the first attempt.
    """
    attempts = max_retries + 1
    last_exc: Optional[Exception] = None
    for i in range(attempts):
        try:
            return await fn()
        except Exception as exc:
            # Catch ``Exception``, not ``BaseException`` — ctrl-C
            # (KeyboardInterrupt) and shutdown (SystemExit) must propagate
            # cleanly; only application errors are candidates for retry.
            last_exc = exc
            if not is_retryable(exc) or i == attempts - 1:
                raise
            # Equal-jitter backoff: sleep in [backoff_ms/2, backoff_ms] ms.
            # Guarantees a non-zero minimum so retries always pause, while
            # still randomizing to avoid thundering herds.
            sleep_ms = random.uniform(backoff_ms / 2, backoff_ms)
            logger.info(
                "dispatch_resilience: retryable %s (attempt %d/%d); sleeping %.0fms",
                type(exc).__name__,
                i + 1,
                attempts,
                sleep_ms,
            )
            await asyncio.sleep(sleep_ms / 1000.0)
    # Defensive; the loop above either returns or raises.
    assert last_exc is not None  # pragma: no cover
    raise last_exc  # pragma: no cover


# ── Circuit breaker ──────────────────────────────────────────────────────────

class CircuitBreaker:
    """Rolling-window failure-count breaker with half-open probe.

    State machine:

        closed  ──(threshold consecutive fails in window)──▶ open
        open    ──(cooldown_seconds elapsed, next allow())──▶ half_open
        half_open ──(success)──▶ closed
        half_open ──(failure)──▶ open
    """

    def __init__(self, *, threshold: int = 5, window_seconds: float = 60, cooldown_seconds: float = 30):
        self.threshold = threshold
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds
        self._state: str = "closed"
        self._failures: Deque[float] = collections.deque()
        self._opened_at: Optional[float] = None

    @property
    def state(self) -> str:
        return self._state

    def reset(self) -> None:
        self._state = "closed"
        self._failures.clear()
        self._opened_at = None

    def allow(self) -> bool:
        """Check if a call is allowed. May transition open → half_open."""
        if self._state == "closed":
            return True
        if self._state == "half_open":
            return True
        # open — explicit runtime check instead of ``assert`` (which
        # disappears under ``python -O`` and would crash with a cryptic
        # TypeError on ``None`` arithmetic below).
        if self._opened_at is None:
            raise RuntimeError(
                "CircuitBreaker state corruption: opened_at is None while state=open"
            )
        if (time.monotonic() - self._opened_at) >= self.cooldown_seconds:
            self._state = "half_open"
            return True
        return False

    def record_success(self) -> None:
        if self._state == "half_open":
            self._state = "closed"
            self._failures.clear()
            self._opened_at = None
            return
        if self._state == "closed":
            # A success in the closed state clears stale failures — this
            # makes "5 consecutive" literally consecutive, not "5 over any
            # rolling window regardless of interleaving successes".
            self._failures.clear()

    def record_failure(self) -> None:
        if self._state == "half_open":
            self._state = "open"
            self._opened_at = time.monotonic()
            return
        now = time.monotonic()
        # Drop failures outside the rolling window.
        while self._failures and (now - self._failures[0]) > self.window_seconds:
            self._failures.popleft()
        self._failures.append(now)
        if len(self._failures) >= self.threshold:
            self._state = "open"
            self._opened_at = now

    def snapshot(self) -> Dict[str, Any]:
        return {
            "state": self._state,
            "failures": len(self._failures),
            "threshold": self.threshold,
            "window_seconds": self.window_seconds,
            "cooldown_seconds": self.cooldown_seconds,
        }


# Module-level singleton. Dispatch imports this directly.
openrouter_breaker = CircuitBreaker(
    threshold=5,
    window_seconds=60,
    cooldown_seconds=30,
)


async def call_with_retry_and_breaker(
    fn: Callable[[], Awaitable[Any]],
    *,
    breaker: CircuitBreaker = openrouter_breaker,
    max_retries: int = 1,
    backoff_ms: int = 500,
) -> Any:
    """Convenience wrapper composing the breaker and retry policies.

    Raises ``CircuitBreakerOpen`` if the breaker is open and not ready for a
    probe. Otherwise runs the call with retries and records success/failure
    against the breaker. Retries on non-retryable exceptions still count as
    a single failure (the first attempt did fail).
    """
    if not breaker.allow():
        raise CircuitBreakerOpen("openrouter breaker open")
    try:
        result = await call_with_retry(
            fn,
            max_retries=max_retries,
            backoff_ms=backoff_ms,
        )
    except BaseException:
        breaker.record_failure()
        raise
    breaker.record_success()
    return result
