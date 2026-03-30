"""Budget / OpenRouter usage tracking helpers."""

import json
import logging
import time
from urllib.request import Request as UrlReq, urlopen as urlopen_

from .config import (
    OPENROUTER_API_KEY, DEEPSEEK_API_KEY, MOONSHOT_API_KEY, TAVILY_API_KEY,
    BILLING_ALERT_BALANCE_USD,
)

logger = logging.getLogger("mission-control")

_budget_cache: tuple[float, dict] = (0, {})


def _fetch_openrouter_usage() -> dict:
    """Fetch usage from OpenRouter API (cached 5 min)."""
    global _budget_cache
    now = time.time()
    if now - _budget_cache[0] < 300 and _budget_cache[1]:
        return _budget_cache[1]
    try:
        req = UrlReq(
            "https://openrouter.ai/api/v1/auth/key",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
        )
        resp = urlopen_(req, timeout=10)
        data = json.loads(resp.read().decode())["data"]
        result = {
            "daily_usd": data.get("usage_daily", 0),
            "weekly_usd": data.get("usage_weekly", 0),
            "monthly_usd": data.get("usage_monthly", 0),
            "total_usd": data.get("usage", 0),
        }
        _budget_cache = (now, result)
        return result
    except Exception as e:
        logger.warning("OpenRouter usage fetch failed: %s", e)
        return _budget_cache[1] if _budget_cache[1] else {"error": str(e)}


# ── Multi-provider balance fetching ──────────────────────────────────────────

_provider_cache: dict[str, tuple[float, dict]] = {}


def _cached_fetch(provider: str, fetch_fn):
    """5-minute cache wrapper."""
    now = time.time()
    cached = _provider_cache.get(provider, (0, {}))
    if now - cached[0] < 300 and cached[1]:
        return cached[1]
    result = fetch_fn()
    _provider_cache[provider] = (now, result)
    return result


# ── DeepSeek ─────────────────────────────────────────────────────────────────


def _fetch_deepseek_balance_inner() -> dict:
    """Fetch balance from DeepSeek API."""
    import sys
    _urlopen = getattr(sys.modules.get("main"), "urlopen_", urlopen_)
    try:
        req = UrlReq(
            "https://api.deepseek.com/user/balance",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
        )
        resp = _urlopen(req, timeout=10)
        data = json.loads(resp.read().decode())
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


_fetch_deepseek_balance.__wrapped__ = _fetch_deepseek_balance_inner


# ── Moonshot ─────────────────────────────────────────────────────────────────


def _fetch_moonshot_balance_inner() -> dict:
    """Fetch balance from Moonshot API."""
    import sys
    _urlopen = getattr(sys.modules.get("main"), "urlopen_", urlopen_)
    try:
        req = UrlReq(
            "https://api.moonshot.ai/v1/users/me/balance",
            headers={"Authorization": f"Bearer {MOONSHOT_API_KEY}"},
        )
        resp = _urlopen(req, timeout=10)
        data = json.loads(resp.read().decode()).get("data", {})
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


_fetch_moonshot_balance.__wrapped__ = _fetch_moonshot_balance_inner


# ── Tavily ───────────────────────────────────────────────────────────────────


def _fetch_tavily_usage_inner() -> dict:
    """Fetch usage from Tavily API."""
    import sys
    _urlopen = getattr(sys.modules.get("main"), "urlopen_", urlopen_)
    try:
        req = UrlReq(
            "https://api.tavily.com/usage",
            headers={"Authorization": f"Bearer {TAVILY_API_KEY}"},
        )
        resp = _urlopen(req, timeout=10)
        data = json.loads(resp.read().decode())
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


_fetch_tavily_usage.__wrapped__ = _fetch_tavily_usage_inner


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
        data["status"] = "error" if "error" in data else "ok"
        results.append(data)
    # Dashboard-only providers (no API)
    for name in ["google_ai", "groq", "zai"]:
        results.append({"provider": name, "status": "dashboard_only"})
    return results
