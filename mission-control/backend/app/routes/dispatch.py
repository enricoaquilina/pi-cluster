"""Dispatch endpoints."""

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import verify_api_key, rate_limit
from ..db import get_db
from ..dispatch_degradation import (
    DegradationLevel,
    compute_degradation,
    degraded_reply,
)
from ..dispatch_resilience import (
    CircuitBreakerOpen,
    call_with_retry_and_breaker,
    openrouter_breaker,
)
from ..event_bus import event_bus
from ..events_log import event_logger
from ..maxwell_prompt import build_system_prompt, prompt_to_string
from ..models.dispatch import DispatchRequest, DispatchResponse, DispatchLogEntry
from ..dispatch_engine import (
    PERSONA_ROUTING,
    rate_limiter, _openclaw_chat, _is_gateway_reachable, _log_dispatch,
)
from ..outbound_guard import guard_reply
from ..trading_helpers import TEAM_ROSTER

logger = logging.getLogger("mission-control")

# Personas whose system prompt is built dynamically at request time from the
# ~/life/ vault. Extend this set when new vault-aware personas are added.
DYNAMIC_SYSTEM_PROMPT_PERSONAS = {"Maxwell"}

# Default kill-file path; the operator can touch/rm this without a restart.
DEFAULT_KILL_FILE = "/var/run/maxwell.disabled"

# Default dead-letter path — captures unhandled exceptions with the raw
# request so they can be replayed later.
DEFAULT_DEAD_LETTER = "/var/log/maxwell/deadletter.jsonl"

router = APIRouter()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _prompt_hash(prompt: str) -> str:
    """Short SHA-256 prefix for privacy-safe event correlation."""
    return hashlib.sha256(prompt.encode("utf-8", errors="replace")).hexdigest()[:16]


def _kill_file_path() -> str:
    return os.environ.get("MAXWELL_KILL_FILE", DEFAULT_KILL_FILE)


def _dead_letter_path() -> str:
    return os.environ.get("MAXWELL_DEAD_LETTER_LOG", DEFAULT_DEAD_LETTER)


def _current_degradation() -> DegradationLevel:
    """Re-evaluate kill switch + breaker + vault per-request (no caching)."""
    return compute_degradation(
        kill_switch_env=os.environ.get("MAXWELL_WHATSAPP_ENABLED", ""),
        kill_switch_file=_kill_file_path(),
        breaker_open=(openrouter_breaker.state == "open"),
    )


def _append_dead_letter(req: DispatchRequest, exc: BaseException) -> None:
    """Append a raw request + error to the dead-letter log.

    Never raises — dead-letter failures must NOT mask the original error
    that triggered dead-letter in the first place. Catches ``Exception``
    broadly (not just ``OSError``) because the path can also fail on
    ``TypeError`` from the serializer, ``UnicodeError`` on encoding,
    ``ValueError`` from an unexpected mock, etc.
    """
    path = _dead_letter_path()
    try:
        parent = Path(path).parent
        parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(parent, 0o700)
        except OSError:
            pass
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "persona": req.persona,
            "prompt": req.prompt,
            "timeout": req.timeout,
            "error": f"{type(exc).__name__}: {exc}",
        }
        line = json.dumps(entry, default=str, separators=(",", ":"))
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except Exception as e:
        logger.warning("dispatch: failed to write dead-letter entry: %s", e)


def _emit_event(
    req: DispatchRequest,
    *,
    status: str,
    degraded_level: DegradationLevel,
    llm_ms: Optional[int],
    guard_hit: bool,
    error_class: Optional[str],
) -> None:
    """Single place every dispatch writes its structured events-log line."""
    event_logger.append({
        "persona": req.persona,
        "status": status,
        "degraded_level": degraded_level.value,
        "llm_ms": llm_ms,
        "prompt_sha256": _prompt_hash(req.prompt),
        "prompt_bytes": len(req.prompt.encode("utf-8", errors="replace")),
        "guard_hit": guard_hit,
        "error_class": error_class,
        "breaker": openrouter_breaker.snapshot(),
    })


def _degraded_response(req: DispatchRequest, level: DegradationLevel, delegate: str) -> DispatchResponse:
    """Build a DispatchResponse that short-circuits the LLM call entirely."""
    text = degraded_reply(level) or ""
    return DispatchResponse(
        persona=req.persona,
        node="gateway",
        delegate=delegate,
        response=text,
        elapsed_ms=0,
        fallback=True,
        original_node=None,
    )


# ── Endpoint ─────────────────────────────────────────────────────────────────

@router.post("/api/dispatch", response_model=DispatchResponse)
async def dispatch_task(req: DispatchRequest, _=Depends(verify_api_key), __=Depends(rate_limit)):
    """Dispatch a prompt to a persona via the OpenClaw gateway.

    Request flow:

    1. Resolve persona or return 400.
    2. Compute degradation (kill switch, breaker, vault reachability).
       L3/L4/L5 short-circuit with a canned reply and emit an events line.
    3. Build the system prompt (dynamic for Maxwell; static otherwise) and
       wrap the user prompt as untrusted for vault-aware personas.
    4. Check gateway reachability and rate limit.
    5. Call the LLM via call_with_retry_and_breaker — one retry on 5xx /
       timeout, circuit breaker trips after 5 consecutive failures.
    6. Pass the response through the outbound guard.
    7. Emit a success events line and return.

    On any unhandled exception, append to the dead-letter log before
    re-raising so the request can be replayed.
    """
    try:
        route = PERSONA_ROUTING.get(req.persona)
        if not route:
            available = sorted(PERSONA_ROUTING.keys())
            raise HTTPException(
                status_code=400,
                detail=f"Unknown persona '{req.persona}'. Available: {available}",
            )

        delegate, static_system_prompt = route

        # Step 2: degradation short-circuits
        level = _current_degradation()
        if level in (DegradationLevel.L5, DegradationLevel.L4, DegradationLevel.L3):
            _emit_event(
                req,
                status="degraded",
                degraded_level=level,
                llm_ms=None,
                guard_hit=False,
                error_class=None,
            )
            return _degraded_response(req, level, delegate)

        # Step 3: build system prompt + wrap user prompt
        if req.persona in DYNAMIC_SYSTEM_PROMPT_PERSONAS:
            system_prompt = prompt_to_string(build_system_prompt(persona=req.persona))
            chat_prompt = f"<untrusted_user_message>\n{req.prompt}\n</untrusted_user_message>"
        else:
            system_prompt = static_system_prompt
            chat_prompt = req.prompt

        # Step 4: gateway reachability
        if not await _is_gateway_reachable():
            _log_dispatch(req.persona, "gateway", delegate, False, req.prompt, "",
                          0, "error", "OpenClaw gateway unreachable")
            _emit_event(
                req,
                status="error",
                degraded_level=DegradationLevel.L0,
                llm_ms=0,
                guard_hit=False,
                error_class="GatewayUnreachable",
            )
            raise HTTPException(status_code=503, detail="OpenClaw gateway is unreachable")

        # Step 4b: rate limit
        try:
            rate_limiter.check()
        except HTTPException as rate_exc:
            _log_dispatch(req.persona, "gateway", delegate, False, req.prompt, "",
                          0, "rate_limited", rate_exc.detail)
            _emit_event(
                req,
                status="error",
                degraded_level=DegradationLevel.L0,
                llm_ms=0,
                guard_hit=False,
                error_class="RateLimitExceeded",
            )
            raise

        # Step 5: LLM call with retry + breaker
        start = datetime.now(timezone.utc)
        try:
            async def _call():
                return await _openclaw_chat(chat_prompt, system_prompt, req.timeout)

            response = await call_with_retry_and_breaker(_call)
        except CircuitBreakerOpen:
            # Breaker tripped during this request; short-circuit to L4.
            _emit_event(
                req,
                status="degraded",
                degraded_level=DegradationLevel.L4,
                llm_ms=0,
                guard_hit=False,
                error_class="CircuitBreakerOpen",
            )
            return _degraded_response(req, DegradationLevel.L4, delegate)
        except HTTPException as e:
            elapsed_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
            _log_dispatch(req.persona, "gateway", delegate, False, req.prompt, "",
                          elapsed_ms, "error", e.detail)
            _emit_event(
                req,
                status="error",
                degraded_level=DegradationLevel.L0,
                llm_ms=elapsed_ms,
                guard_hit=False,
                error_class=f"HTTPException{e.status_code}",
            )
            raise

        # Step 6: outbound guard
        guard_hit, response = guard_reply(response)
        if guard_hit:
            logger.warning(
                "dispatch: outbound_guard replaced hallucinated-path reply for persona=%s",
                req.persona,
            )

        elapsed_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        _log_dispatch(req.persona, "gateway", delegate, False, req.prompt, response, elapsed_ms)

        # Step 7: success event
        _emit_event(
            req,
            status="success",
            degraded_level=DegradationLevel.L0,
            llm_ms=elapsed_ms,
            guard_hit=guard_hit,
            error_class=None,
        )

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

    except HTTPException:
        # HTTPExceptions are the framework's normal error channel —
        # already logged above, let FastAPI translate to the right status.
        raise
    except BaseException as exc:
        # Anything else is truly unexpected: capture forensics, then
        # translate to a clean HTTP 500 so the caller sees a structured
        # error rather than a dropped connection. The original exception
        # is preserved in the dead-letter log for replay / debugging.
        logger.exception("dispatch: unhandled exception for persona=%s", req.persona)
        _append_dead_letter(req, exc)
        _emit_event(
            req,
            status="error",
            degraded_level=DegradationLevel.L0,
            llm_ms=None,
            guard_hit=False,
            error_class=type(exc).__name__,
        )
        raise HTTPException(status_code=500, detail="internal dispatch error")


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
