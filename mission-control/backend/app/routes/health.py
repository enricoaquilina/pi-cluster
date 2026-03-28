"""Health and SSE endpoints."""

import asyncio
import time

import psycopg2
from fastapi import APIRouter, Response
from starlette.responses import StreamingResponse
from fastapi import HTTPException

from ..config import _start_time, DATABASE_URL
from ..event_bus import event_bus

router = APIRouter()


@router.get("/health")
def health(response: Response):
    result = {"status": "ok", "uptime_seconds": int(time.time() - _start_time), "sse_subscribers": event_bus.subscriber_count}
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        result["db"] = "ok"
    except Exception as e:
        result["db"] = f"error: {e}"
        result["status"] = "degraded"
        response.status_code = 503
    return result


@router.get("/api/events")
async def sse_events():
    """Server-Sent Events stream for real-time dashboard updates."""
    try:
        queue = event_bus.subscribe()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Too many SSE connections")

    async def generate():
        try:
            yield "event: connected\ndata: {}\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=25)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            event_bus.unsubscribe(queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
