"""Dispatch engine: OpenClaw gateway routing, rate limiting, dispatch logging."""

import asyncio
import collections
import json
import logging
import os
import time
from typing import Optional

from fastapi import HTTPException

from .db import _pool
from .config import OPENCLAW_GATEWAY_HOST, OPENCLAW_GATEWAY_PORT, OPENCLAW_GATEWAY_TOKEN

logger = logging.getLogger("mission-control")

# OpenClaw gateway config — all dispatches go through the single gateway
# which routes to available compute nodes (slave0, slave1) internally
OPENCLAW_GATEWAY = {
    "host": OPENCLAW_GATEWAY_HOST,
    "port": OPENCLAW_GATEWAY_PORT,
    "token": OPENCLAW_GATEWAY_TOKEN,
}

# Map personas -> (delegate, system_prompt)
# Node assignment removed — the gateway handles routing to slave0/slave1
#
# "Maxwell" is registered here so the dispatch endpoint accepts it, but its
# actual system prompt is built dynamically at request time by
# app.maxwell_prompt.build_system_prompt() — it reads hard-rules, daily note,
# vault inventory, etc. The placeholder string below is never sent to the LLM.
PERSONA_ROUTING = {
    # Orchestrator / meta (dynamic system prompt — see routes/dispatch.py)
    "Maxwell":  ("orchestrator", "__dynamic__"),
    # Engineering (coding-focused)
    "Archie":   ("coder",     "You are Archie, a backend developer. Focus on APIs, databases, and server architecture."),
    "Pixel":    ("coder",     "You are Pixel, a frontend developer. Focus on UI, layouts, and client-side logic."),
    "Harbor":   ("coder",     "You are Harbor, a DevOps engineer. Focus on Docker, CI/CD, and infrastructure."),
    "Sentinel": ("architect", "You are Sentinel, a security engineer. Focus on threat assessment, hardening, and audit."),
    # Content (cost-optimized)
    "Docsworth": ("assistant", "You are Docsworth, a technical writer. Focus on docs, READMEs, and guides."),
    "Stratton":  ("assistant", "You are Stratton, a content strategist. Focus on content planning, SEO, and audience."),
    "Quill":     ("assistant", "You are Quill, a copywriter. Focus on marketing copy and product descriptions."),
    # Design
    "Flux":   ("assistant", "You are Flux, a UI/UX designer. Focus on user flows, wireframes, and interactions."),
    "Chroma": ("assistant", "You are Chroma, a visual designer. Focus on color, typography, and visual systems."),
    "Sigil":  ("assistant", "You are Sigil, a brand designer. Focus on identity, logos, and style guides."),
    # Research
    "Scout":  ("assistant", "You are Scout, a research analyst. Focus on market research and competitive analysis."),
    "Ledger": ("assistant", "You are Ledger, a data analyst. Focus on metrics, dashboards, and reporting."),
}

# ── Rate Limiter ─────────────────────────────────────────────────────────────


class RateLimiter:
    def __init__(self):
        self._windows: dict[str, collections.deque] = {}
        self._limit = 15  # per minute across all dispatches

    def check(self, label: str = "gateway"):
        now = time.monotonic()
        window = self._windows.setdefault(label, collections.deque())
        while window and window[0] < now - 60:
            window.popleft()
        if len(window) >= self._limit:
            raise HTTPException(status_code=429, detail=f"Rate limit exceeded ({self._limit}/min)")
        window.append(now)


rate_limiter = RateLimiter()


async def _openclaw_chat(prompt: str, system_prompt: str, timeout: int) -> str:
    """Send a prompt via OpenRouter API (HTTP) and return the response.
    Uses the same models as the OpenClaw gateway's fallback chain."""
    from urllib.request import Request, urlopen
    from urllib.error import URLError

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=503, detail="OPENROUTER_API_KEY not configured")

    model = "google/gemini-2.5-flash"
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    body = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": 4096,
    }).encode()

    req = Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        resp = await asyncio.to_thread(urlopen, req, timeout=timeout)
        data = json.loads(resp.read().decode())
        choices = data.get("choices", [])
        if not choices:
            raise HTTPException(status_code=502, detail="No response from model")
        return choices[0].get("message", {}).get("content", "")
    except URLError as e:
        raise HTTPException(status_code=502, detail=f"OpenRouter API error: {e}")
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=f"OpenRouter timed out after {timeout}s")


async def _is_gateway_reachable() -> bool:
    """Check if the OpenClaw gateway is reachable."""
    from urllib.request import urlopen
    try:
        resp = urlopen(f"http://{OPENCLAW_GATEWAY['host']}:{OPENCLAW_GATEWAY['port']}/health", timeout=5)
        data = json.loads(resp.read().decode())
        return data.get("ok", False)
    except Exception:
        return False


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
