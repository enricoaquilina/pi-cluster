"""Mission Control API — FastAPI app creation, lifespan, middleware, CORS."""

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import ALLOWED_ORIGINS
from .db import _pool, init_db
from .event_bus import event_bus
from .background import _heartbeat_sweep, _budget_snapshot, _node_snapshot
from .routes import api_router
from .helpers import row_to_dict

logger = logging.getLogger("mission-control")

# Keep row_to_dict accessible as app-level export for backward compat
row_to_dict = row_to_dict


@asynccontextmanager
async def lifespan(a: FastAPI):
    event_bus.set_loop(asyncio.get_running_loop())
    init_db()
    sweep_task = asyncio.create_task(_heartbeat_sweep())
    budget_task = asyncio.create_task(_budget_snapshot())
    node_task = asyncio.create_task(_node_snapshot())
    yield
    for task in (sweep_task, budget_task, node_task):
        task.cancel()
    for task in (sweep_task, budget_task, node_task):
        try:
            await task
        except asyncio.CancelledError:
            pass
    _pool.closeall()


app = FastAPI(title="Mission Control", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request, call_next):
    start = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        logger.error("%s %s 500 (unhandled)", request.method, request.url.path)
        raise
    elapsed = (time.monotonic() - start) * 1000
    if not request.url.path.startswith("/health"):
        logger.info("%s %s %d %.0fms", request.method, request.url.path, response.status_code, elapsed)
    return response


app.include_router(api_router)
