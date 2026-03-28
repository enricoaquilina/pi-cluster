"""Dispatch endpoints."""

from datetime import datetime, timezone
from typing import Optional

import websockets
from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import verify_api_key
from ..db import get_db
from ..event_bus import event_bus
from ..models.dispatch import DispatchRequest, DispatchResponse, DispatchLogEntry
from ..dispatch_engine import (
    PERSONA_ROUTING, ZEROCLAW_NODES, FALLBACK_NODES, FALLBACK_DELEGATE_MAP,
    NODE_MODELS, rate_limiter, _zeroclaw_chat, _is_node_dispatchable, _log_dispatch,
)
from ..trading_helpers import TEAM_ROSTER

import logging
logger = logging.getLogger("mission-control")

router = APIRouter()


@router.post("/api/dispatch", response_model=DispatchResponse)
async def dispatch_task(req: DispatchRequest, _=Depends(verify_api_key)):
    """Dispatch a prompt to a persona's ZeroClaw node with failover."""
    route = PERSONA_ROUTING.get(req.persona)
    if not route:
        available = sorted(PERSONA_ROUTING.keys())
        raise HTTPException(status_code=400, detail=f"Unknown persona '{req.persona}'. Available: {available}")

    node, delegate, system_prompt = route
    is_fallback = False
    original_node = None

    # Health-aware routing with failover
    if not _is_node_dispatchable(node):
        fallback_node = FALLBACK_NODES.get(node)
        if fallback_node and _is_node_dispatchable(fallback_node):
            original_node = node
            delegate_map = FALLBACK_DELEGATE_MAP.get((node, fallback_node), {})
            delegate = delegate_map.get(delegate, ZEROCLAW_NODES[fallback_node]["delegates"][0])
            node = fallback_node
            is_fallback = True
            logger.info("Failover: %s → %s for persona %s", original_node, node, req.persona)
        else:
            _log_dispatch(req.persona, node, delegate, False, req.prompt, "",
                          0, "error", "All nodes unavailable", original_node=None,
                          model=NODE_MODELS.get(node))
            raise HTTPException(status_code=503, detail="All dispatch nodes are unavailable")

    cfg = ZEROCLAW_NODES[node]
    if not cfg["token"]:
        raise HTTPException(status_code=503, detail=f"Node {node} not configured (missing token)")

    # Rate limiting
    try:
        rate_limiter.check(node)
    except HTTPException as rate_exc:
        _log_dispatch(req.persona, node, delegate, is_fallback, req.prompt, "",
                      0, "rate_limited", rate_exc.detail, original_node=original_node,
                      model=NODE_MODELS.get(node))
        raise

    start = datetime.now(timezone.utc)
    try:
        response = await _zeroclaw_chat(node, req.prompt, system_prompt, req.timeout)
    except websockets.exceptions.WebSocketException as e:
        elapsed_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        logger.error("Dispatch to %s failed: %s", node, e)
        _log_dispatch(req.persona, node, delegate, is_fallback, req.prompt, "",
                      elapsed_ms, "error", str(e), original_node=original_node,
                      model=NODE_MODELS.get(node))
        raise HTTPException(status_code=502, detail=f"Cannot reach {node}: {e}")
    except HTTPException as e:
        elapsed_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        _log_dispatch(req.persona, node, delegate, is_fallback, req.prompt, "",
                      elapsed_ms, "error", e.detail, original_node=original_node,
                      model=NODE_MODELS.get(node))
        raise

    elapsed_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
    _log_dispatch(req.persona, node, delegate, is_fallback, req.prompt, response,
                  elapsed_ms, original_node=original_node, model=NODE_MODELS.get(node))

    event_bus.publish("dispatch")
    return DispatchResponse(
        persona=req.persona,
        node=node,
        delegate=delegate,
        response=response,
        elapsed_ms=elapsed_ms,
        fallback=is_fallback,
        original_node=original_node,
    )


@router.get("/api/dispatch/personas")
def list_personas():
    """List available personas and their routing info."""
    result = {}
    for persona, (node, delegate, _) in PERSONA_ROUTING.items():
        team = "unknown"
        for t in TEAM_ROSTER["teams"]:
            if any(m["name"] == persona for m in t["members"]):
                team = t["name"]
                break
        result[persona] = {"node": node, "delegate": delegate, "team": team}
    return result


@router.post("/api/dispatch/log", status_code=201)
def post_dispatch_log(entry: DispatchLogEntry, conn=Depends(get_db), _=Depends(verify_api_key)):
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
