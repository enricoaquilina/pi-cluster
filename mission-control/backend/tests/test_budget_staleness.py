"""Tests for budget cache staleness handling.

Verifies that _cached_fetch correctly handles API failures: returning stale
data with _stale marker, not storing stale snapshots, and surfacing staleness
through the /api/budget endpoint. Also includes regression tests to prevent
silent stale data from recurring across providers.
"""

import os
import time
from unittest.mock import patch
from urllib.error import URLError

import pytest

# Markers
requires_cluster = pytest.mark.skipif(
    not os.path.exists("/mnt/external/mission-control"),
    reason="Requires cluster environment (skipped in CI)",
)


# ── Fixture: save/restore cache state ───────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_provider_cache():
    """Save and restore _provider_cache and _budget_cache around each test."""
    from app.budget_helpers import _provider_cache, _budget_cache
    saved_cache = dict(_provider_cache)
    saved_budget = _budget_cache
    yield
    # Restore
    from app import budget_helpers
    budget_helpers._provider_cache.clear()
    budget_helpers._provider_cache.update(saved_cache)
    budget_helpers._budget_cache = saved_budget


# ── Helpers ─────────────────────────────────────────────────────────────────


SAMPLE_USAGE = {"daily_usd": 0.5, "weekly_usd": 1.0, "monthly_usd": 3.0, "total_usd": 10.0}


def _seed_cache(provider, data, age_seconds=0):
    """Seed _provider_cache with data at a given age."""
    from app import budget_helpers
    budget_helpers._provider_cache[provider] = (time.time() - age_seconds, data)


def _clear_cache(provider):
    from app import budget_helpers
    budget_helpers._provider_cache.pop(provider, None)


# ═══════════════════════════════════════════════════════════════════════════
# Core cache behavior (no mocking — uses controlled lambdas)
# ═══════════════════════════════════════════════════════════════════════════


def test_cached_fetch_returns_fresh_data():
    """_cached_fetch stores and returns fresh data from fetch_fn."""
    from app.budget_helpers import _cached_fetch, _provider_cache
    _clear_cache("test_fresh")
    result = _cached_fetch("test_fresh", lambda: {"value": 1})
    assert result == {"value": 1}
    assert "test_fresh" in _provider_cache
    assert _provider_cache["test_fresh"][1] == {"value": 1}


def test_cached_fetch_returns_cached_within_ttl():
    """_cached_fetch returns cached data within 5-minute TTL without calling fetch_fn."""
    from app.budget_helpers import _cached_fetch
    _seed_cache("test_ttl", {"value": 1}, age_seconds=0)
    call_count = 0

    def counting_fetch():
        nonlocal call_count
        call_count += 1
        return {"value": 99}

    result = _cached_fetch("test_ttl", counting_fetch)
    assert result == {"value": 1}, "Should return cached, not fresh"
    assert call_count == 0, "fetch_fn should not have been called"


def test_cached_fetch_retries_after_ttl():
    """_cached_fetch calls fetch_fn when TTL has expired."""
    from app.budget_helpers import _cached_fetch, _provider_cache
    _seed_cache("test_retry", {"value": 1}, age_seconds=600)
    result = _cached_fetch("test_retry", lambda: {"value": 2})
    assert result == {"value": 2}
    assert _provider_cache["test_retry"][1] == {"value": 2}


def test_cached_fetch_marks_stale_on_error_with_cache():
    """When fetch fails and good cache exists, return stale data with _stale marker."""
    from app.budget_helpers import _cached_fetch, _provider_cache
    _seed_cache("test_stale", {"value": 42}, age_seconds=600)
    before_time = time.time()
    result = _cached_fetch("test_stale", lambda: {"error": "api down"})
    assert result["value"] == 42
    assert result["_stale"] is True
    assert "error" not in result
    # Verify retry timer was reset (timestamp is recent)
    cached_ts = _provider_cache["test_stale"][0]
    assert cached_ts >= before_time, "Cache timestamp should be updated"


def test_cached_fetch_returns_error_without_cache():
    """When fetch fails and no cache exists, return the error dict."""
    from app.budget_helpers import _cached_fetch
    _clear_cache("test_nodata")
    result = _cached_fetch("test_nodata", lambda: {"error": "api down"})
    assert result == {"error": "api down"}


def test_cached_fetch_clears_stale_on_recovery():
    """When API recovers, stale marker is cleared from returned data."""
    from app.budget_helpers import _cached_fetch
    _seed_cache("test_recover", {"value": 1, "_stale": True}, age_seconds=600)
    result = _cached_fetch("test_recover", lambda: {"value": 2})
    assert result == {"value": 2}
    assert "_stale" not in result


def test_cached_fetch_stale_resets_retry_timer():
    """After marking stale, subsequent call within TTL returns cached stale data (no retry)."""
    from app.budget_helpers import _cached_fetch
    _seed_cache("test_timer", {"value": 1}, age_seconds=600)
    call_count = 0

    def counting_error():
        nonlocal call_count
        call_count += 1
        return {"error": "fail"}

    # First call: TTL expired, fetch fails, returns stale
    r1 = _cached_fetch("test_timer", counting_error)
    assert r1["_stale"] is True
    assert call_count == 1

    # Second call: within new TTL, should NOT retry
    r2 = _cached_fetch("test_timer", counting_error)
    assert r2["_stale"] is True
    assert call_count == 1, "Should not have retried within TTL"


# ═══════════════════════════════════════════════════════════════════════════
# Inner function error handling
# ═══════════════════════════════════════════════════════════════════════════


def test_openrouter_inner_returns_error_on_failure():
    """_fetch_openrouter_usage_inner returns error dict on API failure, no cache fallback."""
    from app.budget_helpers import _fetch_openrouter_usage_inner
    with patch("app.budget_helpers.urlopen_", side_effect=URLError("Connection refused")):
        result = _fetch_openrouter_usage_inner()
    assert "error" in result
    assert "Connection refused" in result["error"]


# ═══════════════════════════════════════════════════════════════════════════
# Endpoint integration (seed cache directly, no service mocking)
# ═══════════════════════════════════════════════════════════════════════════


async def test_budget_endpoint_surfaces_stale_flag(client):
    """GET /api/budget returns stale=true when cached data is stale."""
    _seed_cache("openrouter", {**SAMPLE_USAGE, "_stale": True}, age_seconds=0)
    resp = await client.get("/api/budget")
    assert resp.status_code == 200
    data = resp.json()
    assert data["stale"] is True


async def test_budget_endpoint_strips_stale_from_usage(client):
    """GET /api/budget does not leak _stale into the usage dict."""
    _seed_cache("openrouter", {**SAMPLE_USAGE, "_stale": True}, age_seconds=0)
    resp = await client.get("/api/budget")
    data = resp.json()
    assert "_stale" not in data["usage"]
    # Verify usage values are still present
    assert data["usage"]["daily_usd"] == 0.5
    assert data["usage"]["monthly_usd"] == 3.0


async def test_stale_marker_survives_multiple_endpoint_calls(client):
    """Calling GET /api/budget twice doesn't corrupt the cache (mutation guard)."""
    _seed_cache("openrouter", {**SAMPLE_USAGE, "_stale": True}, age_seconds=0)
    resp1 = await client.get("/api/budget")
    resp2 = await client.get("/api/budget")
    assert resp1.json()["stale"] is True
    assert resp2.json()["stale"] is True


def test_all_provider_balances_strips_stale():
    """_fetch_all_provider_balances strips _stale and sets status='stale'."""
    from app import budget_helpers
    _seed_cache("openrouter", {**SAMPLE_USAGE, "_stale": True}, age_seconds=0)
    original_key = budget_helpers.OPENROUTER_API_KEY
    try:
        budget_helpers.OPENROUTER_API_KEY = "test-key"
        balances = budget_helpers._fetch_all_provider_balances()
        or_entry = next(b for b in balances if b["provider"] == "openrouter")
        assert or_entry["status"] == "stale"
        assert "_stale" not in or_entry
        assert or_entry["daily_usd"] == 0.5
    finally:
        budget_helpers.OPENROUTER_API_KEY = original_key


# ═══════════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════════


def test_cached_fetch_empty_cache_entry_returns_error():
    """Empty dict in cache is falsy — no stale fallback, returns error."""
    from app.budget_helpers import _cached_fetch
    _seed_cache("test_empty", {}, age_seconds=600)
    result = _cached_fetch("test_empty", lambda: {"error": "fail"})
    assert "error" in result
    assert "_stale" not in result


def test_cached_fetch_error_in_cache_forces_retry():
    """Error dict in cache is not treated as valid — TTL check forces retry."""
    from app.budget_helpers import _cached_fetch
    _seed_cache("test_errorcache", {"error": "old error"}, age_seconds=0)
    result = _cached_fetch("test_errorcache", lambda: {"value": 99})
    assert result == {"value": 99}, "Should have retried despite valid TTL"


async def test_budget_endpoint_503_when_no_cache(client):
    """GET /api/budget returns 503 when API fails and no cached data exists."""
    _clear_cache("openrouter")
    with patch("app.budget_helpers.urlopen_", side_effect=URLError("Connection refused")):
        resp = await client.get("/api/budget")
    assert resp.status_code == 503


# ═══════════════════════════════════════════════════════════════════════════
# Regression prevention
# ═══════════════════════════════════════════════════════════════════════════


def test_all_inner_fetch_functions_return_error_key_on_failure():
    """Every provider inner function must return {"error": ...} on failure.

    This prevents the root cause of the staleness bug: an inner function
    silently returning cached data without an error marker. If a new provider
    is added, add its inner function here.
    """
    from app.budget_helpers import (
        _fetch_openrouter_usage_inner,
        _fetch_deepseek_balance_inner,
        _fetch_moonshot_balance_inner,
        _fetch_tavily_usage_inner,
    )
    with patch("app.budget_helpers.urlopen_", side_effect=URLError("test failure")):
        for name, fn in [
            ("openrouter", _fetch_openrouter_usage_inner),
            ("deepseek", _fetch_deepseek_balance_inner),
            ("moonshot", _fetch_moonshot_balance_inner),
            ("tavily", _fetch_tavily_usage_inner),
        ]:
            result = fn()
            assert "error" in result, (
                f"{name} inner function returned {result} on failure — "
                "must include 'error' key to prevent silent stale caching"
            )


def test_cached_fetch_never_caches_error_as_fresh():
    """Error responses must never be treated as valid cache hits."""
    from app.budget_helpers import _cached_fetch, _provider_cache
    _clear_cache("test_errfresh")
    # First call: fetch fails, error cached
    _cached_fetch("test_errfresh", lambda: {"error": "fail"})

    # Manually ensure TTL is still valid
    ts = _provider_cache["test_errfresh"][0]
    assert time.time() - ts < 300, "TTL should still be valid"

    # Second call: despite valid TTL, error entry should force retry
    call_count = 0

    def counting_fetch():
        nonlocal call_count
        call_count += 1
        return {"value": 42}

    result = _cached_fetch("test_errfresh", counting_fetch)
    assert call_count == 1, "Error cache should not be treated as valid — must retry"
    assert result == {"value": 42}


def test_cached_fetch_preserves_data_through_repeated_stale_cycles():
    """Repeated stale cycles don't corrupt or lose original data values."""
    from app.budget_helpers import _cached_fetch, _provider_cache
    _seed_cache("test_cycles", {"value": 42, "extra": "data"}, age_seconds=600)

    for i in range(3):
        result = _cached_fetch("test_cycles", lambda: {"error": f"fail_{i}"})
        assert result["value"] == 42, f"Cycle {i}: original value lost"
        assert result["extra"] == "data", f"Cycle {i}: extra field lost"
        assert result["_stale"] is True, f"Cycle {i}: stale marker missing"
        # Expire the cache for next cycle
        ts, data = _provider_cache["test_cycles"]
        _provider_cache["test_cycles"] = (ts - 600, data)
