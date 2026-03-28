"""Budget / cost tracking endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query

from ..config import BUDGET_DAILY, BUDGET_WEEKLY, BUDGET_MONTHLY, BUDGET_ALERT_THRESHOLD
from ..db import get_db
from ..budget_helpers import _fetch_openrouter_usage

router = APIRouter()


@router.get("/api/budget")
def get_budget(conn=Depends(get_db)):
    """Current OpenRouter spend vs budget limits, with 7-day history summary."""
    usage = _fetch_openrouter_usage()
    if "error" in usage:
        raise HTTPException(503, detail=usage["error"])
    result = {
        "usage": usage,
        "limits": {"daily": BUDGET_DAILY, "weekly": BUDGET_WEEKLY, "monthly": BUDGET_MONTHLY},
        "remaining": {
            "daily": round(BUDGET_DAILY - usage["daily_usd"], 2),
            "weekly": round(BUDGET_WEEKLY - usage["weekly_usd"], 2),
            "monthly": round(BUDGET_MONTHLY - usage["monthly_usd"], 2),
        },
        "alerts": {
            "daily": usage["daily_usd"] >= BUDGET_DAILY * BUDGET_ALERT_THRESHOLD,
            "weekly": usage["weekly_usd"] >= BUDGET_WEEKLY * BUDGET_ALERT_THRESHOLD,
            "monthly": usage["monthly_usd"] >= BUDGET_MONTHLY * BUDGET_ALERT_THRESHOLD,
        },
    }
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COALESCE(MAX(daily_usd), 0),
                       COALESCE(AVG(daily_usd), 0),
                       COUNT(*)
                FROM budget_snapshots
                WHERE snapshot_at > now() - interval '7 days'
            """)
            peak, avg, count = cur.fetchone()
        result["history"] = {
            "peak_daily_7d": round(float(peak), 4),
            "avg_daily_7d": round(float(avg), 4),
            "snapshots_7d": count,
        }
    except Exception:
        result["history"] = {"peak_daily_7d": 0, "avg_daily_7d": 0, "snapshots_7d": 0}
    return result


@router.get("/api/budget/history")
def budget_history(days: int = Query(7, ge=1, le=90), conn=Depends(get_db)):
    """Historical budget snapshots for trend analysis."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT daily_usd, weekly_usd, monthly_usd, total_usd,
                   daily_limit, weekly_limit, monthly_limit, snapshot_at
            FROM budget_snapshots
            WHERE snapshot_at > now() - interval '1 day' * %s
            ORDER BY snapshot_at ASC
            LIMIT 500
        """, (days,))
        cols = ["daily_usd", "weekly_usd", "monthly_usd", "total_usd",
                "daily_limit", "weekly_limit", "monthly_limit", "snapshot_at"]
        rows = cur.fetchall()
    return [{**dict(zip(cols, r)), "snapshot_at": r[7].isoformat()} for r in rows]
