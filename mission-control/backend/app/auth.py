"""API key authentication and rate limiting."""

import time
from collections import deque
from typing import Optional

from fastapi import Header, HTTPException, Request

from .config import API_KEY


def verify_api_key(x_api_key: Optional[str] = Header(None)):
    if not API_KEY:
        return
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


class GlobalRateLimiter:
    """Per-IP sliding-window rate limiting for mutation endpoints."""

    def __init__(self, max_per_minute: int = 60):
        self._max = max_per_minute
        self._windows: dict[str, deque] = {}

    def check(self, client_ip: str):
        now = time.monotonic()
        window = self._windows.get(client_ip)
        if window:
            while window and window[0] < now - 60:
                window.popleft()
            if not window:
                del self._windows[client_ip]
                window = None
        if window is None:
            window = deque()
            self._windows[client_ip] = window
        if len(window) >= self._max:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        window.append(now)


_global_limiter = GlobalRateLimiter(max_per_minute=60)


def rate_limit(request: Request):
    """FastAPI dependency — rate-limits mutation endpoints per client IP."""
    _global_limiter.check(request.client.host if request.client else "unknown")
