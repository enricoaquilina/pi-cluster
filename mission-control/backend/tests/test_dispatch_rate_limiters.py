"""Tests for per-persona and hourly dispatch rate limiters."""

import time


def test_per_persona_allows_under_limit():
    from app.dispatch_engine import PerPersonaRateLimiter

    limiter = PerPersonaRateLimiter(max_per_hour=5)
    for _ in range(5):
        assert limiter.check("Archie") is True


def test_per_persona_rejects_over_limit():
    from app.dispatch_engine import PerPersonaRateLimiter

    limiter = PerPersonaRateLimiter(max_per_hour=3)
    for _ in range(3):
        limiter.check("Archie")
    assert limiter.check("Archie") is False


def test_per_persona_independent_across_personas():
    from app.dispatch_engine import PerPersonaRateLimiter

    limiter = PerPersonaRateLimiter(max_per_hour=2)
    assert limiter.check("Archie") is True
    assert limiter.check("Archie") is True
    assert limiter.check("Archie") is False
    # Different persona should be independent
    assert limiter.check("Harbor") is True


def test_hourly_allows_under_limit():
    from app.dispatch_engine import HourlyRateLimiter

    limiter = HourlyRateLimiter(max_per_hour=10)
    for _ in range(10):
        assert limiter.check() is True


def test_hourly_rejects_over_limit():
    from app.dispatch_engine import HourlyRateLimiter

    limiter = HourlyRateLimiter(max_per_hour=3)
    for _ in range(3):
        limiter.check()
    assert limiter.check() is False


def test_hourly_window_cleanup():
    """Timestamps older than 1 hour are cleaned up."""
    from app.dispatch_engine import HourlyRateLimiter

    limiter = HourlyRateLimiter(max_per_hour=2)
    # Inject old timestamps (>1 hour ago)
    limiter._timestamps = [time.time() - 7200, time.time() - 7200]
    # Should allow new requests after cleanup
    assert limiter.check() is True


def test_per_persona_window_cleanup():
    """Timestamps older than 1 hour are cleaned up per persona."""
    from app.dispatch_engine import PerPersonaRateLimiter

    limiter = PerPersonaRateLimiter(max_per_hour=2)
    # Inject old timestamps
    limiter._counts["Archie"] = [time.time() - 7200, time.time() - 7200]
    assert limiter.check("Archie") is True
