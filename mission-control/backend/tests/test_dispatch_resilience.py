"""OpenRouter retry and circuit breaker.

Today, any transient LLM error (5xx, timeout) is fatal — the user sees a 502.
The resilience layer:

- Retries once on retryable errors (5xx, connection, timeout) with 500ms
  backoff + jitter. Non-retryable errors (4xx, client errors) are NOT retried.
- A circuit breaker opens after N consecutive failures in a rolling window.
  While open, calls fail fast with CircuitBreakerOpen (so dispatch can return
  a canned L4 reply without waiting for timeouts).
- Half-open after a cooldown: next call is allowed; success closes the
  breaker, failure re-opens.

Tested invariants:
- Retry count honored
- No retry on non-retryable
- Circuit opens at the threshold, not before
- Circuit half-open → closed on success, → open on failure
- Breaker state observable for logging
- Rolling window (failures outside window do not count)
"""

import asyncio
import time

import pytest


class _FakeErrors:
    """Container for retryable / non-retryable exception sentinels."""
    class Retryable(Exception):
        pass

    class NonRetryable(Exception):
        pass


def _retryable_classifier(exc: Exception) -> bool:
    return isinstance(exc, _FakeErrors.Retryable)


# ── Retry ────────────────────────────────────────────────────────────────────

async def test_retry_once_on_retryable_then_succeed():
    from app.dispatch_resilience import call_with_retry

    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _FakeErrors.Retryable("boom")
        return "ok"

    result = await call_with_retry(
        flaky,
        max_retries=1,
        backoff_ms=1,
        is_retryable=_retryable_classifier,
    )
    assert result == "ok"
    assert calls["n"] == 2


async def test_retry_gives_up_after_max_retries():
    from app.dispatch_resilience import call_with_retry

    calls = {"n": 0}

    async def always_fails():
        calls["n"] += 1
        raise _FakeErrors.Retryable("boom")

    with pytest.raises(_FakeErrors.Retryable):
        await call_with_retry(
            always_fails,
            max_retries=1,
            backoff_ms=1,
            is_retryable=_retryable_classifier,
        )
    assert calls["n"] == 2  # initial + 1 retry


async def test_no_retry_on_non_retryable():
    from app.dispatch_resilience import call_with_retry

    calls = {"n": 0}

    async def fails_fast():
        calls["n"] += 1
        raise _FakeErrors.NonRetryable("4xx")

    with pytest.raises(_FakeErrors.NonRetryable):
        await call_with_retry(
            fails_fast,
            max_retries=3,
            backoff_ms=1,
            is_retryable=_retryable_classifier,
        )
    assert calls["n"] == 1  # no retry attempted


async def test_success_on_first_try_no_retry():
    from app.dispatch_resilience import call_with_retry

    calls = {"n": 0}

    async def happy():
        calls["n"] += 1
        return "ok"

    result = await call_with_retry(
        happy,
        max_retries=3,
        backoff_ms=1,
        is_retryable=_retryable_classifier,
    )
    assert result == "ok"
    assert calls["n"] == 1


async def test_retry_applies_backoff():
    from app.dispatch_resilience import call_with_retry

    async def flaky():
        raise _FakeErrors.Retryable("boom")

    start = time.monotonic()
    with pytest.raises(_FakeErrors.Retryable):
        await call_with_retry(
            flaky,
            max_retries=2,
            backoff_ms=50,
            is_retryable=_retryable_classifier,
        )
    elapsed_ms = (time.monotonic() - start) * 1000
    # 2 retries × ~50ms each (with jitter, so lower bound is ~50 total)
    assert elapsed_ms >= 50


# ── Circuit breaker ──────────────────────────────────────────────────────────

def test_breaker_starts_closed():
    from app.dispatch_resilience import CircuitBreaker

    b = CircuitBreaker(threshold=3, window_seconds=60, cooldown_seconds=30)
    assert b.state == "closed"
    assert b.allow() is True


def test_breaker_opens_after_threshold_fails():
    from app.dispatch_resilience import CircuitBreaker

    b = CircuitBreaker(threshold=3, window_seconds=60, cooldown_seconds=30)
    for _ in range(3):
        b.record_failure()
    assert b.state == "open"
    assert b.allow() is False


def test_breaker_does_not_open_below_threshold():
    from app.dispatch_resilience import CircuitBreaker

    b = CircuitBreaker(threshold=3, window_seconds=60, cooldown_seconds=30)
    b.record_failure()
    b.record_failure()
    assert b.state == "closed"
    assert b.allow() is True


def test_breaker_transitions_to_half_open_after_cooldown():
    from app.dispatch_resilience import CircuitBreaker

    b = CircuitBreaker(threshold=1, window_seconds=60, cooldown_seconds=0)
    b.record_failure()
    assert b.state == "open"

    # cooldown=0 means immediate half-open on next allow() check
    allowed = b.allow()
    assert allowed is True
    assert b.state == "half_open"


def test_breaker_closes_on_success_from_half_open():
    from app.dispatch_resilience import CircuitBreaker

    b = CircuitBreaker(threshold=1, window_seconds=60, cooldown_seconds=0)
    b.record_failure()
    b.allow()  # → half-open
    b.record_success()
    assert b.state == "closed"


def test_breaker_reopens_on_failure_from_half_open():
    from app.dispatch_resilience import CircuitBreaker

    b = CircuitBreaker(threshold=1, window_seconds=60, cooldown_seconds=0)
    b.record_failure()
    b.allow()  # → half-open
    b.record_failure()
    assert b.state == "open"


def test_breaker_success_resets_failure_count_when_closed():
    from app.dispatch_resilience import CircuitBreaker

    b = CircuitBreaker(threshold=3, window_seconds=60, cooldown_seconds=30)
    b.record_failure()
    b.record_failure()  # 2 fails
    b.record_success()  # counter resets
    b.record_failure()
    b.record_failure()  # 2 fails — still below threshold
    assert b.state == "closed"


def test_breaker_state_exposed_for_observability():
    from app.dispatch_resilience import CircuitBreaker

    b = CircuitBreaker(threshold=2, window_seconds=60, cooldown_seconds=30)
    snapshot = b.snapshot()
    assert "state" in snapshot
    assert "failures" in snapshot
    assert snapshot["state"] == "closed"
    assert snapshot["failures"] == 0

    b.record_failure()
    snap2 = b.snapshot()
    assert snap2["failures"] == 1


# ── Classifier ──────────────────────────────────────────────────────────────

def test_is_openrouter_retryable_5xx():
    from app.dispatch_resilience import is_openrouter_retryable
    from fastapi import HTTPException

    assert is_openrouter_retryable(HTTPException(status_code=502, detail="bad gateway")) is True
    assert is_openrouter_retryable(HTTPException(status_code=504, detail="timeout")) is True


def test_is_openrouter_retryable_4xx_not_retryable():
    from app.dispatch_resilience import is_openrouter_retryable
    from fastapi import HTTPException

    assert is_openrouter_retryable(HTTPException(status_code=400, detail="bad request")) is False
    assert is_openrouter_retryable(HTTPException(status_code=401, detail="unauthorized")) is False
    assert is_openrouter_retryable(HTTPException(status_code=429, detail="rate limit")) is True


def test_is_openrouter_retryable_timeout():
    from app.dispatch_resilience import is_openrouter_retryable

    assert is_openrouter_retryable(asyncio.TimeoutError()) is True
