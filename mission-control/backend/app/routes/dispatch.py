"""Dispatch endpoints."""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import verify_api_key, rate_limit
from ..db import get_db
from ..event_bus import event_bus
from ..models.dispatch import DispatchRequest, DispatchResponse, DispatchLogEntry
from ..dispatch_engine import (
    PERSONA_ROUTING, OPENCLAW_GATEWAY,
    rate_limiter, _openclaw_chat, _is_gateway_reachable, _log_dispatch,
)
from ..trading_helpers import TEAM_ROSTER

import logging
logger = logging.getLogger("mission-control")

router = APIRouter()


@router.post("/api/dispatch", response_model=DispatchResponse)
async def dispatch_task(req: DispatchRequest, _=Depends(verify_api_key), __=Depends(rate_limit)):
    """Dispatch a prompt to a persona via the OpenClaw gateway."""
    route = PERSONA_ROUTING.get(req.persona)
    if not route:
        available = sorted(PERSONA_ROUTING.keys())
        raise HTTPException(status_code=400, detail=f"Unknown persona '{req.persona}'. Available: {available}")

    delegate, system_prompt = route

    # Check gateway reachability
    if not await _is_gateway_reachable():
        _log_dispatch(req.persona, "gateway", delegate, False, req.prompt, "",
                      0, "error", "OpenClaw gateway unreachable")
        raise HTTPException(status_code=503, detail="OpenClaw gateway is unreachable")

    # Rate limiting
    try:
        rate_limiter.check()
    except HTTPException as rate_exc:
        _log_dispatch(req.persona, "gateway", delegate, False, req.prompt, "",
                      0, "rate_limited", rate_exc.detail)
        raise

    start = datetime.now(timezone.utc)
    try:
        response = await _openclaw_chat(req.prompt, system_prompt, req.timeout)
    except HTTPException as e:
        elapsed_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        _log_dispatch(req.persona, "gateway", delegate, False, req.prompt, "",
                      elapsed_ms, "error", e.detail)
        raise

    elapsed_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
    _log_dispatch(req.persona, "gateway", delegate, False, req.prompt, response, elapsed_ms)

    event_bus.publish("dispatch")
    return DispatchResponse(
        persona=req.persona,
        node="gateway",
        delegate=delegate,
        response=response,
        elapsed_ms=elapsed_ms,
        fallback=False,
        original_node=None,
    )


@router.get("/api/dispatch/personas")
def list_personas():
    """List available personas and their routing info."""
    result = {}
    for persona, (delegate, _) in PERSONA_ROUTING.items():
        team = "unknown"
        for t in TEAM_ROSTER["teams"]:
            if any(m["name"] == persona for m in t["members"]):
                team = t["name"]
                break
        result[persona] = {"node": "gateway", "delegate": delegate, "team": team}
    return result


@router.post("/api/dispatch/log", status_code=201)
def post_dispatch_log(entry: DispatchLogEntry, conn=Depends(get_db), _=Depends(verify_api_key), __=Depends(rate_limit)):
    """Record a dispatch from the cluster service."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO dispatch_log
               (persona, node, delegate, fallback, prompt_preview,
                response_preview, elapsed_ms, status, error_detail)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id, created_at""",
            (entry.persona, entry.node, entry.delegate, entry.fallback,
             entry.prompt_preview[:500], entry.response_preview[:500],
             entry.elapsed_ms, entry.status, entry.error_detail),
        )
        row = cur.fetchone()
    conn.commit()
    event_bus.publish("dispatch")
    return {"id": row[0], "created_at": row[1].isoformat() if row[1] else None}


@router.get("/api/dispatch/log")
def get_dispatch_log(
    persona: Optional[str] = Query(None),
    node: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn=Depends(get_db),
):
    """Query dispatch history."""
    clauses = []
    params: list = []
    if persona:
        clauses.append("persona = %s")
        params.append(persona)
    if node:
        clauses.append("node = %s")
        params.append(node)
    if status:
        clauses.append("status = %s")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM dispatch_log {where}", params)
        total = cur.fetchone()[0]
        cur.execute(
            f"""SELECT id, persona, node, delegate, fallback, original_node,
                       prompt_preview, response_preview, elapsed_ms, status,
                       error_detail, created_at
                FROM dispatch_log {where}
                ORDER BY created_at DESC LIMIT %s OFFSET %s""",
            params + [limit, offset],
        )
        cols = ["id", "persona", "node", "delegate", "fallback", "original_node",
                "prompt_preview", "response_preview", "elapsed_ms", "status",
                "error_detail", "created_at"]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return {"items": rows, "total": total, "limit": limit, "offset": offset}
