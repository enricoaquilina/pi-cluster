"""SSE Event Bus for real-time dashboard updates."""

import asyncio
import json
import logging
import threading
import time

logger = logging.getLogger("mission-control")


class EventBus:
    """In-process pub/sub for SSE — thread-safe for sync FastAPI endpoints."""

    MAX_CLIENTS = 20

    def __init__(self):
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        with self._lock:
            if len(self._subscribers) >= self.MAX_CLIENTS:
                raise RuntimeError("Too many SSE clients")
            q: asyncio.Queue = asyncio.Queue(maxsize=64)
            self._subscribers.add(q)
            logger.info("SSE client connected (%d total)", len(self._subscribers))
            return q

    def unsubscribe(self, q: asyncio.Queue):
        with self._lock:
            self._subscribers.discard(q)
            logger.info("SSE client disconnected (%d total)", len(self._subscribers))

    def publish(self, event: str):
        """Thread-safe publish — uses call_soon_threadsafe for sync endpoints."""
        payload = json.dumps({"event": event, "ts": time.time()})
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._safe_put, q, payload)
            else:
                self._safe_put(q, payload)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    @staticmethod
    def _safe_put(q: asyncio.Queue, payload: str):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            logger.warning("SSE queue full, dropping event for slow client")


event_bus = EventBus()
