"""Dispatch engine: ZeroClaw node routing, failover, rate limiting."""

import asyncio
import collections
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import websockets
from fastapi import HTTPException

from .db import _pool

logger = logging.getLogger("mission-control")

# Node connection config — loaded from /etc/zeroclaw-tokens.env at startup
ZEROCLAW_NODES = {
    "slave0": {
        "host": "192.168.0.3",
        "port": 18790,
        "token": os.environ.get("ZEROCLAW_SLAVE0_TOKEN", ""),
        "delegates": ["coder", "architect"],
    },
    "slave1": {
        "host": "192.168.0.4",
        "port": 18791,
        "token": os.environ.get("ZEROCLAW_SLAVE1_TOKEN", ""),
        "delegates": ["assistant", "tester"],
    },
}

# Map personas -> (node, delegate_agent, system_prompt)
PERSONA_ROUTING = {
    # Engineering -> slave0 (Claude Sonnet)
    "Archie":   ("slave0", "coder",     "You are Archie, a backend developer. Focus on APIs, databases, and server architecture."),
    "Pixel":    ("slave0", "coder",     "You are Pixel, a frontend developer. Focus on UI, layouts, and client-side logic."),
    "Harbor":   ("slave0", "coder",     "You are Harbor, a DevOps engineer. Focus on Docker, CI/CD, and infrastructure."),
    "Sentinel": ("slave0", "architect", "You are Sentinel, a security engineer. Focus on threat assessment, hardening, and audit."),
    # Content -> slave1 (DeepSeek)
    "Docsworth": ("slave1", "assistant", "You are Docsworth, a technical writer. Focus on docs, READMEs, and guides."),
    "Stratton":  ("slave1", "assistant", "You are Stratton, a content strategist. Focus on content planning, SEO, and audience."),
    "Quill":     ("slave1", "assistant", "You are Quill, a copywriter. Focus on marketing copy and product descriptions."),
    # Design -> slave1 (DeepSeek)
    "Flux":   ("slave1", "assistant", "You are Flux, a UI/UX designer. Focus on user flows, wireframes, and interactions."),
    "Chroma": ("slave1", "assistant", "You are Chroma, a visual designer. Focus on color, typography, and visual systems."),
    "Sigil":  ("slave1", "assistant", "You are Sigil, a brand designer. Focus on identity, logos, and style guides."),
    # Research -> slave1 (DeepSeek)
    "Scout":  ("slave1", "assistant", "You are Scout, a research analyst. Focus on market research and competitive analysis."),
    "Ledger": ("slave1", "assistant", "You are Ledger, a data analyst. Focus on metrics, dashboards, and reporting."),
}

# ── Failover Config ──────────────────────────────────────────────────────────

NODE_MODELS = {"slave0": "openrouter/google/gemini-2.5-flash", "slave1": "openrouter/google/gemini-2.5-flash", "heavy": "openrouter/google/gemini-2.5-flash", "master": "openrouter/google/gemini-2.5-flash"}
FALLBACK_NODES = {"slave0": "slave1", "slave1": "slave0"}
FALLBACK_DELEGATE_MAP = {
    ("slave0", "slave1"): {"coder": "assistant", "architect": "assistant"},
    ("slave1", "slave0"): {"assistant": "coder", "tester": "coder"},
}


# ── Rate Limiter ─────────────────────────────────────────────────────────────

class RateLimiter:
    def __init__(self):
        self._windows: dict[str, collections.deque] = {}
        self._limits = {"slave0": 10, "slave1": 5}  # per minute

    def check(self, node: str):
        limit = self._limits.get(node, 10)
        now = time.monotonic()
        window = self._windows.setdefault(node, collections.deque())
        # Purge entries older than 60s
        while window and window[0] < now - 60:
            window.popleft()
        if len(window) >= limit:
            raise HTTPException(status_code=429, detail=f"Rate limit exceeded for {node} ({limit}/min)")
        window.append(now)


rate_limiter = RateLimiter()
_last_smoke_trigger = 0.0


async def _zeroclaw_chat(node: str, prompt: str, system_prompt: str, timeout: int) -> str:
    """Send a prompt to a ZeroClaw node via WebSocket and return the response."""
    cfg = ZEROCLAW_NODES[node]
    uri = f"ws://{cfg['host']}:{cfg['port']}/ws/chat?token={cfg['token']}"

    async with websockets.connect(uri, open_timeout=10) as ws:
        message = prompt
        if system_prompt:
            message = f"[System: {system_prompt}]\n\n{prompt}"
        await ws.send(json.dumps({"type": "message", "content": message}))

        # Collect response chunks until 'done'
        full_response = ""
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
                data = json.loads(msg)
                msg_type = data.get("type", "")
                if msg_type == "done":
                    full_response = data.get("full_response", full_response)
                    break
                elif msg_type == "error":
                    raise HTTPException(status_code=502, detail=data.get("message", "ZeroClaw error"))
                elif msg_type in ("text", "content", "chunk"):
                    full_response += data.get("content", data.get("text", ""))
            except asyncio.TimeoutError:
                if full_response:
                    break
                raise HTTPException(status_code=504, detail=f"ZeroClaw {node} timed out after {timeout}s")

    return full_response


def _is_node_dispatchable(node_name: str) -> bool:
    """Check if a node is healthy enough to dispatch to."""
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT status, last_heartbeat FROM nodes WHERE name = %s""",
                (node_name,),
            )
            row = cur.fetchone()
        if not row:
            return False
        status, last_hb = row
        if status not in ("healthy", "online"):
            return False
        if last_hb is None:
            return False
        age = (datetime.now(timezone.utc) - last_hb.replace(tzinfo=timezone.utc if last_hb.tzinfo is None else last_hb.tzinfo)).total_seconds()
        return age < 600  # 10 minutes
    except Exception:
        return True  # If DB is down, don't block dispatch
    finally:
        _pool.putconn(conn)


def _log_dispatch(persona: str, node: str, delegate: str, fallback: bool,
                  prompt: str, response: str, elapsed_ms: int,
                  status: str = "success", error_detail: Optional[str] = None,
                  original_node: Optional[str] = None, model: Optional[str] = None):
    """Log a dispatch attempt to the database."""
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO dispatch_log
                   (persona, node, delegate, fallback, original_node, prompt_preview,
                    response_preview, elapsed_ms, status, error_detail, model)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (persona, node, delegate, fallback, original_node,
                 prompt[:500], response[:500],
                 elapsed_ms, status, error_detail, model),
            )
        conn.commit()
    except Exception as e:
        logger.warning("Failed to log dispatch: %s", e)
    finally:
        _pool.putconn(conn)
