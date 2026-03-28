"""Budget / OpenRouter usage tracking helpers."""

import json
import logging
import time

from .config import OPENROUTER_API_KEY

logger = logging.getLogger("mission-control")

_budget_cache: tuple[float, dict] = (0, {})


def _fetch_openrouter_usage() -> dict:
    """Fetch usage from OpenRouter API (cached 5 min)."""
    global _budget_cache
    now = time.time()
    if now - _budget_cache[0] < 300 and _budget_cache[1]:
        return _budget_cache[1]
    try:
        from urllib.request import Request as UrlReq, urlopen as urlopen_
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
