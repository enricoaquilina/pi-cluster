"""Dispatch endpoints."""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import verify_api_key, rate_limit
from ..db import get_db
from ..event_bus import event_bus
from ..maxwell_prompt import build_system_prompt, prompt_to_string
from ..models.dispatch import DispatchRequest, DispatchResponse, DispatchLogEntry
from ..dispatch_engine import (
    PERSONA_ROUTING,
    rate_limiter, _openclaw_chat, _is_gateway_reachable, _log_dispatch,
)
from ..outbound_guard import guard_reply
from ..trading_helpers import TEAM_ROSTER

import logging
logger = logging.getLogger("mission-control")

# Personas whose system prompt is built dynamically at request time from the
# ~/life/ vault. Extend this set when new vault-aware personas are added.
DYNAMIC_SYSTEM_PROMPT_PERSONAS = {"Maxwell"}

router = APIRouter()


@router.post("/api/dispatch", response_model=DispatchResponse)
async def dispatch_task(req: DispatchRequest, _=Depends(verify_api_key), __=Depends(rate_limit)):
    """Dispatch a prompt to a persona via the OpenClaw gateway.

    For personas in DYNAMIC_SYSTEM_PROMPT_PERSONAS (Maxwell), the system prompt
    is rebuilt from the ~/life/ vault per-request and the user prompt is wrapped
    in <untrusted_user_message> tags. The response is then passed through the
    outbound regex guard so any hallucinated filesystem paths are replaced with
    a safe fallback before the caller sees them.
    """
    route = PERSONA_ROUTING.get(req.persona)
    if not route:
        available = sorted(PERSONA_ROUTING.keys())
        raise HTTPException(status_code=400, detail=f"Unknown persona '{req.persona}'. Available: {available}")

    delegate, static_system_prompt = route

    # Build the system prompt. For vault-aware personas, assemble from the
    # knowledge base and wrap the user prompt as untrusted input. Preserve the
    # original persona casing so per-persona IDENTITY dirs like
    # ~/.openclaw/workspace/Maxwell/IDENTITY.md are found by the builder.
    if req.persona in DYNAMIC_SYSTEM_PROMPT_PERSONAS:
        system_prompt = prompt_to_string(build_system_prompt(persona=req.persona))
        chat_prompt = f"<untrusted_user_message>\n{req.prompt}\n</untrusted_user_message>"
    else:
        system_prompt = static_system_prompt
        chat_prompt = req.prompt

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
        response = await _openclaw_chat(chat_prompt, system_prompt, req.timeout)
    except HTTPException as e:
        elapsed_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        _log_dispatch(req.persona, "gateway", delegate, False, req.prompt, "",
                      elapsed_ms, "error", e.detail)
        raise

    # Outbound regex guard — last line of defense against known-bad path
    # hallucinations. If a match is found, replace the reply with a safe
    # fallback; dispatch_log still records the (guarded) preview.
    guard_hit, response = guard_reply(response)
    if guard_hit:
        logger.warning(
            "dispatch: outbound_guard replaced hallucinated-path reply for persona=%s",
            req.persona,
        )

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
