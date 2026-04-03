"""Budget / OpenRouter usage tracking helpers."""

import json
import logging
import time
from urllib.request import Request as UrlReq, urlopen as urlopen_

from .config import (
    OPENROUTER_API_KEY, DEEPSEEK_API_KEY, MOONSHOT_API_KEY, TAVILY_API_KEY,
)

logger = logging.getLogger("mission-control")

# Unified TTL cache for all provider fetches (5-minute TTL)
_provider_cache: dict[str, tuple[float, dict]] = {}
# Backward-compat alias — some tests reference _budget_cache directly
_budget_cache: tuple[float, dict] = (0, {})


def _cached_fetch(provider: str, fetch_fn):
    """5-minute cache wrapper for any provider fetch.

    On success: caches fresh data and returns it.
    On error (result contains "error" key):
      - If good cached data exists: returns it with _stale=True, resets retry timer.
      - Otherwise: returns the error dict.
    TTL check skips entries with "error" key to force retry after error-only cache.
    """
    global _budget_cache
    now = time.time()
    entry = _provider_cache.get(provider)

    # Return cached if TTL hasn't expired and data is good
    if entry and now - entry[0] < 300 and entry[1] and "error" not in entry[1]:
        return entry[1]

    result = fetch_fn()

    if "error" not in result:
        # Success — cache fresh data
        result.pop("_stale", None)
        _provider_cache[provider] = (now, result)
        if provider == "openrouter":
            _budget_cache = (now, result)
        return result

    # Fetch failed — return stale cached data if available
    if entry and entry[1] and "error" not in entry[1]:
        clean = {k: v for k, v in entry[1].items() if k != "_stale"}
        stale = {**clean, "_stale": True}
        _provider_cache[provider] = (now, stale)  # reset retry timer
        return stale

    # No good cached data at all
    _provider_cache[provider] = (now, result)
    return result


def _fetch_provider_json(url: str, api_key: str) -> dict:
    """GET url with Bearer auth, return parsed JSON. Tests can patch urlopen_."""
    req = UrlReq(url, headers={"Authorization": f"Bearer {api_key}"})
    resp = urlopen_(req, timeout=10)
    return json.loads(resp.read().decode())


# ── OpenRouter ───────────────────────────────────────────────────────────────


def _fetch_openrouter_usage_inner() -> dict:
    try:
        data = _fetch_provider_json(
            "https://openrouter.ai/api/v1/auth/key", OPENROUTER_API_KEY
        )["data"]
        return {
            "daily_usd": data.get("usage_daily", 0),
            "weekly_usd": data.get("usage_weekly", 0),
            "monthly_usd": data.get("usage_monthly", 0),
            "total_usd": data.get("usage", 0),
        }
    except Exception as e:
        logger.warning("OpenRouter usage fetch failed: %s", e)
        return {"error": str(e)}


def _fetch_openrouter_usage() -> dict:
    return _cached_fetch("openrouter", _fetch_openrouter_usage_inner)


# ── DeepSeek ─────────────────────────────────────────────────────────────────


def _fetch_deepseek_balance_inner() -> dict:
    try:
        data = _fetch_provider_json(
            "https://api.deepseek.com/user/balance", DEEPSEEK_API_KEY
        )
        balance_infos = data.get("balance_infos", [])
        usd_info = next((b for b in balance_infos if b.get("currency") == "USD"), {})
        balance = float(usd_info.get("total_balance", 0))
        return {
            "provider": "deepseek",
            "balance_usd": balance,
            "is_available": data.get("is_available", balance > 0),
        }
    except Exception as e:
        logger.warning("DeepSeek balance fetch failed: %s", e)
        return {"provider": "deepseek", "error": str(e)}


def _fetch_deepseek_balance() -> dict:
    return _cached_fetch("deepseek", _fetch_deepseek_balance_inner)


# ── Moonshot ─────────────────────────────────────────────────────────────────


def _fetch_moonshot_balance_inner() -> dict:
    try:
        data = _fetch_provider_json(
            "https://api.moonshot.ai/v1/users/me/balance", MOONSHOT_API_KEY
        ).get("data", {})
        return {
            "provider": "moonshot",
            "balance_usd": data.get("available_balance", 0),
            "cash_usd": data.get("cash_balance", 0),
            "voucher_usd": data.get("voucher_balance", 0),
        }
    except Exception as e:
        logger.warning("Moonshot balance fetch failed: %s", e)
        return {"provider": "moonshot", "error": str(e)}


def _fetch_moonshot_balance() -> dict:
    return _cached_fetch("moonshot", _fetch_moonshot_balance_inner)


# ── Tavily ───────────────────────────────────────────────────────────────────


def _fetch_tavily_usage_inner() -> dict:
    try:
        data = _fetch_provider_json(
            "https://api.tavily.com/usage", TAVILY_API_KEY
        )
        key_data = data.get("key", {})
        account_data = data.get("account", {})
        return {
            "provider": "tavily",
            "used": key_data.get("usage", 0),
            "limit": key_data.get("limit", 0),
            "plan": account_data.get("current_plan", "unknown"),
        }
    except Exception as e:
        logger.warning("Tavily usage fetch failed: %s", e)
        return {"provider": "tavily", "error": str(e)}


def _fetch_tavily_usage() -> dict:
    return _cached_fetch("tavily", _fetch_tavily_usage_inner)


# ── Alert helpers ────────────────────────────────────────────────────────────


def _check_balance_alert(balance: float, threshold: float) -> bool:
    """True if balance is below 20% of threshold (low funds)."""
    return threshold > 0 and balance < threshold * 0.2


def _check_usage_alert(used: int, limit: int) -> bool:
    """True if usage exceeds 80% of limit."""
    return limit > 0 and used >= limit * 0.8


# ── Aggregate fetch ──────────────────────────────────────────────────────────


def _fetch_all_provider_balances() -> list[dict]:
    """Fetch balances from all configured providers."""
    results = []
    providers = [
        ("openrouter", OPENROUTER_API_KEY,
         lambda: {**_fetch_openrouter_usage(), "provider": "openrouter"}),
        ("deepseek", DEEPSEEK_API_KEY, _fetch_deepseek_balance),
        ("moonshot", MOONSHOT_API_KEY, _fetch_moonshot_balance),
        ("tavily", TAVILY_API_KEY, _fetch_tavily_usage),
    ]
    for name, key, fetch_fn in providers:
        if not key:
            results.append({"provider": name, "status": "no_key"})
            continue
        data = fetch_fn()
        is_stale = data.pop("_stale", False)
        data["status"] = "error" if "error" in data else ("stale" if is_stale else "ok")
        results.append(data)
    for name in ["google_ai", "groq", "zai"]:
        results.append({"provider": name, "status": "dashboard_only"})
    return results
