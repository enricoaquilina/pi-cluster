"""Trading dashboard endpoints."""

from typing import Optional

from fastapi import APIRouter, Query

from ..trading_helpers import (
    _read_json_cached, _compute_copybot_stats,
    _compute_spreadbot_stats, _compute_scalper_stats,
)

router = APIRouter()


@router.get("/api/trading/overview")
def trading_overview():
    control = _read_json_cached("control.json")
    positions = _read_json_cached("positions.json")
    trades = _read_json_cached("paper_trades.json")
    spread_control = _read_json_cached("spread_control.json")
    spread_state = _read_json_cached("spread_state.json")
    scalp_state = _read_json_cached("scalp_state.json")

    cb = _compute_copybot_stats(control, positions, trades)
    sb = _compute_spreadbot_stats(spread_control, spread_state)
    sc = _compute_scalper_stats(spread_control, scalp_state)

    total_pnl = cb["total_pnl"] + sb["settled_pnl"] + sc["total_pnl"]
    total_positions = cb["position_count"] + sb["state_counts"].get("LOCKED", 0) + sb["state_counts"].get("PARTIAL", 0) + sc["active_count"]

    # Recent activity: merge last trades from both bots
    recent = []
    for t in (trades or [])[-20:]:
        recent.append({
            "time": t.get("detected_at", ""),
            "bot": "copybot",
            "action": "COPY" if t.get("executed") else "SKIP",
            "detail": f"{t.get('trader', '?')} {t.get('side', '?')} on \"{t.get('market_title', '?')[:50]}\"",
            "result": t.get("paper_result"),
        })
    for p in (spread_state or [])[-20:]:
        recent.append({
            "time": p.get("updated_at", p.get("created_at", "")),
            "bot": "spreadbot",
            "action": p.get("state", "?"),
            "detail": f"\"{p.get('market_title', '?')[:50]}\"",
            "pnl": p.get("pnl"),
        })
    recent.sort(key=lambda x: x.get("time", ""), reverse=True)

    return {
        "total_pnl": round(total_pnl, 2),
        "total_positions": total_positions,
        "copybot": cb,
        "spreadbot": sb,
        "scalper": sc,
        "daily_budget_total": cb["daily_budget_usd"] + sb["daily_budget_usd"],
        "daily_spent_total": round(cb["daily_spent_usd"] + sb["daily_spent_usd"], 2),
        "recent_activity": recent[:20],
    }


@router.get("/api/trading/copybot/summary")
def copybot_summary():
    control = _read_json_cached("control.json")
    positions = _read_json_cached("positions.json")
    trades = _read_json_cached("paper_trades.json")
    return _compute_copybot_stats(control, positions, trades)


@router.get("/api/trading/copybot/positions")
def copybot_positions():
    positions = _read_json_cached("positions.json")
    if positions is None:
        return []
    for p in positions:
        entry = p.get("entry_price", 0)
        current = p.get("current_price", 0)
        size = p.get("size", 0)
        if current > 0:
            p["computed_pnl"] = round((current - entry) * size, 2)
        else:
            p["computed_pnl"] = None
    return positions


@router.get("/api/trading/copybot/trades")
def copybot_trades(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    trades = _read_json_cached("paper_trades.json")
    if trades is None:
        return {"items": [], "total": 0}
    # Reverse for most recent first
    trades_rev = list(reversed(trades))
    return {"items": trades_rev[offset:offset + limit], "total": len(trades)}


@router.get("/api/trading/copybot/traders")
def copybot_traders():
    trades = _read_json_cached("paper_trades.json")
    if not trades:
        return []

    trader_stats: dict[str, dict] = {}
    for t in trades:
        trader = t.get("trader", "unknown")
        if trader not in trader_stats:
            trader_stats[trader] = {"trader": trader, "total": 0, "executed": 0, "wins": 0, "losses": 0, "skipped": 0}
        s = trader_stats[trader]
        s["total"] += 1
        if t.get("executed"):
            s["executed"] += 1
            if t.get("paper_result") == "WIN":
                s["wins"] += 1
            elif t.get("paper_result") == "LOSS":
                s["losses"] += 1
        else:
            s["skipped"] += 1

    result = list(trader_stats.values())
    for s in result:
        resolved = s["wins"] + s["losses"]
        s["win_rate"] = round(s["wins"] / resolved * 100, 1) if resolved else 0
    result.sort(key=lambda x: x["executed"], reverse=True)
    return result


@router.get("/api/trading/spreadbot/summary")
def spreadbot_summary():
    spread_control = _read_json_cached("spread_control.json")
    spread_state = _read_json_cached("spread_state.json")
    return _compute_spreadbot_stats(spread_control, spread_state)


@router.get("/api/trading/spreadbot/pairs")
def spreadbot_pairs(
    state: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    pairs = _read_json_cached("spread_state.json")
    if pairs is None:
        return {"items": [], "total": 0}
    if state and state != "all":
        pairs = [p for p in pairs if p.get("state") == state.upper()]
    pairs_rev = list(reversed(pairs))
    return {"items": pairs_rev[offset:offset + limit], "total": len(pairs)}


@router.get("/api/trading/scalper/summary")
def scalper_summary():
    spread_control = _read_json_cached("spread_control.json")
    scalp_state = _read_json_cached("scalp_state.json")
    return _compute_scalper_stats(spread_control, scalp_state)


@router.get("/api/trading/scalper/positions")
def scalper_positions():
    positions = _read_json_cached("scalp_state.json")
    return positions if positions else []


@router.get("/api/trading/backtest")
def trading_backtest():
    report = _read_json_cached("backtest_report.json", subdir="backtest")
    leaderboard = _read_json_cached("leaderboard_backtest_report.json", subdir="backtest")
    control = _read_json_cached("control.json")
    enabled = control.get("enabled_traders", []) if control else []
    return {
        "report": report or [],
        "leaderboard": leaderboard or [],
        "enabled_traders": enabled,
    }
