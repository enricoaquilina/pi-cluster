"""Trading dashboard helpers: cached JSON reads and stats computation."""

import json
import logging
import time

from .config import POLYBOT_DATA

logger = logging.getLogger("mission-control")

_trading_cache: dict[str, tuple[float, any]] = {}
TRADING_CACHE_TTL = 30  # seconds


def _read_json_cached(filename: str, subdir: str = "") -> any:
    """Read a JSON file from polybot-data with 30s in-memory cache."""
    key = f"{subdir}/{filename}" if subdir else filename
    now = time.time()
    cached = _trading_cache.get(key)
    if cached and now - cached[0] < TRADING_CACHE_TTL:
        return cached[1]
    path = POLYBOT_DATA / subdir / filename if subdir else POLYBOT_DATA / filename
    try:
        data = json.loads(path.read_text())
        _trading_cache[key] = (now, data)
        return data
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning("Failed to read %s: %s", path, e)
        return None


def _compute_copybot_stats(control, positions, trades):
    """Compute copybot summary statistics."""
    positions = positions or []
    trades = trades or []
    control = control or {}

    total_unrealized = sum(
        (p.get("current_price", 0) - p.get("entry_price", 0)) * p.get("size", 0)
        for p in positions if not p.get("resolved")
    )

    executed = [t for t in trades if t.get("executed")]
    resolved = [t for t in executed if t.get("paper_result") in ("WIN", "LOSS")]
    wins = sum(1 for t in resolved if t["paper_result"] == "WIN")
    win_rate = round(wins / len(resolved) * 100, 1) if resolved else 0

    return {
        "mode": control.get("mode", "unknown"),
        "order_size_usd": round(control.get("order_size_usd", 0), 2),
        "daily_budget_usd": control.get("daily_budget_usd", 0),
        "daily_spent_usd": round(control.get("daily_spent_usd", 0), 2),
        "daily_date": control.get("daily_date", ""),
        "stop_loss_balance_usd": control.get("stop_loss_balance_usd", 0),
        "initial_balance_usd": control.get("initial_balance_usd", 0),
        "max_total_positions": control.get("max_total_positions", 0),
        "enabled_traders": control.get("enabled_traders", []),
        "position_count": len([p for p in positions if not p.get("resolved")]),
        "total_positions": len(positions),
        "unrealized_pnl": round(total_unrealized, 2),
        "total_trades": len(executed),
        "resolved_trades": len(resolved),
        "wins": wins,
        "losses": len(resolved) - wins,
        "win_rate": win_rate,
    }


def _compute_spreadbot_stats(spread_control, spread_state):
    """Compute spreadbot summary statistics."""
    sc = spread_control or {}
    pairs = spread_state or []

    state_counts = {}
    for p in pairs:
        s = p.get("state", "UNKNOWN")
        state_counts[s] = state_counts.get(s, 0) + 1

    settled_pnl = sum(p.get("pnl", 0) for p in pairs if p.get("state") in ("SETTLED", "CANCELLED"))
    active_exposure = sum(
        p.get("cost_usd", 0) for p in pairs if p.get("state") in ("PENDING", "PARTIAL", "LOCKED")
    )

    return {
        "mode": sc.get("mode", "unknown"),
        "order_size_usd": sc.get("order_size_usd", 0),
        "daily_budget_usd": sc.get("daily_budget_usd", 0),
        "daily_spent_usd": round(sc.get("daily_spent_usd", 0), 2),
        "max_pairs": sc.get("max_pairs", 0),
        "max_exposure_usd": sc.get("max_exposure_usd", 0),
        "state_counts": state_counts,
        "total_pairs": len(pairs),
        "settled_pnl": round(settled_pnl, 2),
        "active_exposure": round(active_exposure, 2),
        "base_spread": sc.get("base_spread", 0),
        "fee_free_only": sc.get("fee_free_only", True),
    }


def _compute_scalper_stats(spread_control, scalp_state):
    """Compute scalper summary statistics."""
    sc = spread_control or {}
    positions = scalp_state or []

    active = [p for p in positions if p.get("state") in ("PENDING", "OPEN")]
    closed = [p for p in positions if p.get("state") == "CLOSED"]

    total_pnl = sum(p.get("pnl", 0) for p in closed)
    wins = sum(1 for p in closed if p.get("pnl", 0) > 0)
    win_rate = round(wins / len(closed) * 100, 1) if closed else 0

    close_reasons = {}
    for p in closed:
        reason = p.get("close_reason", "unknown")
        close_reasons[reason] = close_reasons.get(reason, 0) + 1

    return {
        "enabled": sc.get("scalp_enabled", False),
        "mode": sc.get("mode", "unknown"),
        "max_concurrent": sc.get("max_concurrent_scalps", 5),
        "target_cents": sc.get("scalp_target_cents", 0.03),
        "stop_loss_cents": sc.get("scalp_stop_loss_cents", 0.03),
        "max_hold_minutes": sc.get("scalp_max_hold_minutes", 20),
        "daily_budget_usd": sc.get("scalp_daily_budget_usd", 0),
        "daily_spent_usd": round(sc.get("scalp_daily_spent_usd", 0), 2),
        "order_size_usd": sc.get("scalp_order_size_usd", 0),
        "active_count": len(active),
        "closed_count": len(closed),
        "total_pnl": round(total_pnl, 2),
        "wins": wins,
        "losses": len(closed) - wins,
        "win_rate": win_rate,
        "close_reasons": close_reasons,
        "exposure": sum(p.get("cost_usd", 0) for p in active),
    }


TEAM_ROSTER = {
    "orchestrator": {
        "name": "Maxwell",
        "emoji": "\U0001f9e0",
        "role": "Chief of Staff",
        "description": "Orchestrates all subagent teams and coordinates task execution",
    },
    "teams": [
        {
            "name": "Engineering",
            "members": [
                {"name": "Archie", "emoji": "\U0001f3d7\ufe0f", "role": "Backend Developer", "description": "APIs, databases, server architecture"},
                {"name": "Pixel", "emoji": "\U0001f3a8", "role": "Frontend Developer", "description": "UI, layouts, client-side logic"},
                {"name": "Harbor", "emoji": "\U0001f433", "role": "DevOps Engineer", "description": "Docker, CI/CD, infrastructure"},
                {"name": "Sentinel", "emoji": "\U0001f6e1\ufe0f", "role": "Security Engineer", "description": "Threat assessment, hardening, audit"},
            ],
        },
        {
            "name": "Content",
            "members": [
                {"name": "Docsworth", "emoji": "\U0001f4dd", "role": "Technical Writer", "description": "Docs, READMEs, guides"},
                {"name": "Stratton", "emoji": "\U0001f4c8", "role": "Content Strategist", "description": "Content planning, SEO, audience"},
                {"name": "Quill", "emoji": "\u270d\ufe0f", "role": "Copywriter", "description": "Marketing copy, product descriptions"},
            ],
        },
        {
            "name": "Design",
            "members": [
                {"name": "Flux", "emoji": "\U0001f9e9", "role": "UI/UX Designer", "description": "User flows, wireframes, interactions"},
                {"name": "Chroma", "emoji": "\U0001f308", "role": "Visual Designer", "description": "Color, typography, visual systems"},
                {"name": "Sigil", "emoji": "\u2b50", "role": "Brand Designer", "description": "Identity, logos, style guides"},
            ],
        },
        {
            "name": "Research & Analysis",
            "members": [
                {"name": "Scout", "emoji": "\U0001f50d", "role": "Research Analyst", "description": "Market research, competitive analysis"},
                {"name": "Ledger", "emoji": "\U0001f4ca", "role": "Data Analyst", "description": "Metrics, dashboards, reporting"},
            ],
        },
    ],
}
