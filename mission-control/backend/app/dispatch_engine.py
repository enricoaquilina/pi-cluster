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

# Map personas -> (delegate, system_prompt, model)
# Node assignment removed — the gateway handles routing to slave0/slave1
#
# "Maxwell" is registered here so the dispatch endpoint accepts it, but its
# actual system prompt is built dynamically at request time by
# app.maxwell_prompt.build_system_prompt() — it reads hard-rules, daily note,
# vault inventory, etc. The placeholder string below is never sent to the LLM.
DEFAULT_MODEL = "google/gemini-2.5-flash"

PERSONA_ROUTING = {
    # All personas now get vault-grounded dynamic prompts built by
    # maxwell_prompt.build_system_prompt() with per-persona segment selection.
    # The "__dynamic__" placeholder is never sent to the LLM.
    "Maxwell":   ("orchestrator", "__dynamic__", "openai/gpt-5.5"),
    "Archie":    ("coder",        "__dynamic__", "deepseek/deepseek-v4"),
    "Pixel":     ("coder",        "__dynamic__", "qwen/qwen-3-27b"),
    "Harbor":    ("coder",        "__dynamic__", "deepseek/deepseek-v4"),
    "Sentinel":  ("architect",    "__dynamic__", "openai/gpt-5.5"),
    "Docsworth": ("assistant",    "__dynamic__", "qwen/qwen-3-27b"),
    "Stratton":  ("assistant",    "__dynamic__", "zhipu-ai/glm-5.1"),
    "Quill":     ("assistant",    "__dynamic__", "qwen/qwen-3-27b"),
    "Flux":      ("assistant",    "__dynamic__", "zhipu-ai/glm-5.1"),
    "Chroma":    ("assistant",    "__dynamic__", "moonshot/kimi-k2.6"),
    "Sigil":     ("assistant",    "__dynamic__", "zhipu-ai/glm-5.1"),
    "Scout":     ("assistant",    "__dynamic__", "moonshot/kimi-k2.6"),
    "Ledger":    ("assistant",    "__dynamic__", "deepseek/deepseek-v4"),
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

# ── Per-persona and hourly rate limiters (v3) ────────────────────────────────

DISPATCH_HOURLY_LIMIT = int(os.getenv("DISPATCH_HOURLY_LIMIT", "30"))
DISPATCH_PER_PERSONA_LIMIT = int(os.getenv("DISPATCH_PER_PERSONA_LIMIT", "5"))


class HourlyRateLimiter:
    """Global hourly dispatch budget."""

    def __init__(self, max_per_hour: int = DISPATCH_HOURLY_LIMIT):
        self.max_per_hour = max_per_hour
        self._timestamps: list[float] = []

    def check(self) -> bool:
        now = time.time()
        self._timestamps = [t for t in self._timestamps if now - t < 3600]
        if len(self._timestamps) >= self.max_per_hour:
            return False
        self._timestamps.append(now)
        return True

    def reset(self) -> None:
        """Clear all tracked timestamps (for testing)."""
        self._timestamps.clear()


class PerPersonaRateLimiter:
    """Per-persona hourly dispatch limit."""

    def __init__(self, max_per_hour: int = DISPATCH_PER_PERSONA_LIMIT):
        self.max_per_hour = max_per_hour
        self._counts: dict[str, list[float]] = {}

    def check(self, persona: str) -> bool:
        now = time.time()
        window = self._counts.setdefault(persona, [])
        window[:] = [t for t in window if now - t < 3600]
        if len(window) >= self.max_per_hour:
            return False
        window.append(now)
        return True

    def reset(self) -> None:
        """Clear all tracked timestamps (for testing)."""
        self._counts.clear()


hourly_limiter = HourlyRateLimiter()
persona_limiter = PerPersonaRateLimiter()


async def _openclaw_chat(prompt: str, system_prompt: str, timeout: int,
                         model: str | None = None) -> str:
    """Send a prompt via OpenRouter API (HTTP) and return the response.

    Falls back to DEFAULT_MODEL if the requested model returns a 404."""
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=503, detail="OPENROUTER_API_KEY not configured")

    model = model or DEFAULT_MODEL
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    def _build_request(m: str) -> Request:
        body = json.dumps({
            "model": m,
            "messages": messages,
            "max_tokens": 4096,
        }).encode()
        return Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    try:
        try:
            resp = await asyncio.to_thread(urlopen, _build_request(model), timeout=timeout)
        except HTTPError as e:
            if e.code == 404 and model != DEFAULT_MODEL:
                logger.warning("dispatch: model %s returned 404, falling back to %s",
                               model, DEFAULT_MODEL)
                resp = await asyncio.to_thread(urlopen, _build_request(DEFAULT_MODEL), timeout=timeout)
            else:
                raise
        data = json.loads(resp.read().decode())
        choices = data.get("choices", [])
        if not choices:
            raise HTTPException(status_code=502, detail="No response from model")
        return choices[0].get("message", {}).get("content", "")
    except HTTPError as e:
        raise HTTPException(status_code=502, detail=f"OpenRouter API error: {e}")
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
