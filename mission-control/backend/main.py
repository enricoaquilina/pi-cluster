"""Mission Control API — task board for Enrico & Maxwell."""

import asyncio
import collections
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras
import websockets
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

logger = logging.getLogger("mission-control")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logger.addHandler(_handler)
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logger.addHandler(_handler)
logger = logging.getLogger("mission-control")

DATABASE_URL = os.environ["DATABASE_URL"]
POLYBOT_DATA = Path(os.environ.get("POLYBOT_DATA_DIR", "/polybot-data"))
API_KEY = os.environ.get("API_KEY", "")
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
OPENCLAW_DIR = Path(os.environ.get("OPENCLAW_DIR", "/openclaw"))


# ── SSE Event Bus ─────────────────────────────────────────────────────────────


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

    @staticmethod
    def _safe_put(q: asyncio.Queue, payload: str):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            logger.warning("SSE queue full, dropping event for slow client")


event_bus = EventBus()

# ── DB helpers ────────────────────────────────────────────────────────────────

psycopg2.extras.register_uuid()


def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    title       TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    status      TEXT NOT NULL DEFAULT 'todo',
                    priority    TEXT NOT NULL DEFAULT 'medium',
                    assignee    TEXT NOT NULL DEFAULT 'enrico',
                    project     TEXT NOT NULL DEFAULT '',
                    tags        TEXT[] NOT NULL DEFAULT '{}',
                    due_date    TIMESTAMPTZ,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
                CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee);
                CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project);

                CREATE TABLE IF NOT EXISTS dispatch_log (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    persona TEXT NOT NULL,
                    node TEXT NOT NULL,
                    delegate TEXT NOT NULL,
                    fallback BOOLEAN NOT NULL DEFAULT FALSE,
                    original_node TEXT,
                    prompt_preview TEXT NOT NULL DEFAULT '',
                    response_preview TEXT NOT NULL DEFAULT '',
                    elapsed_ms INT NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'success',
                    error_detail TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                -- Add original_node column if upgrading from older schema
                DO $$ BEGIN
                    ALTER TABLE dispatch_log ADD COLUMN original_node TEXT;
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$;
                CREATE INDEX IF NOT EXISTS idx_dispatch_log_created ON dispatch_log(created_at DESC);

                CREATE TABLE IF NOT EXISTS nodes (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    name TEXT UNIQUE NOT NULL,
                    hostname TEXT NOT NULL,
                    hardware TEXT NOT NULL DEFAULT '',
                    framework TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'offline',
                    ram_total_mb INT NOT NULL DEFAULT 0,
                    ram_used_mb INT NOT NULL DEFAULT 0,
                    cpu_percent FLOAT NOT NULL DEFAULT 0,
                    last_heartbeat TIMESTAMPTZ,
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );

                CREATE TABLE IF NOT EXISTS service_checks (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    service TEXT NOT NULL,
                    status TEXT NOT NULL,
                    response_ms INT,
                    error TEXT,
                    checked_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                CREATE INDEX IF NOT EXISTS idx_service_checks_lookup
                    ON service_checks (service, checked_at DESC);

                CREATE TABLE IF NOT EXISTS service_alerts (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    service TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT,
                    downtime_seconds INT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                CREATE INDEX IF NOT EXISTS idx_service_alerts_lookup
                    ON service_alerts (created_at DESC);
            """)
        conn.commit()
    finally:
        conn.close()


# ── App ───────────────────────────────────────────────────────────────────────


HEARTBEAT_STALE_SECONDS = int(os.environ.get("HEARTBEAT_STALE_SECONDS", "120"))


async def _heartbeat_sweep():
    """Periodically mark nodes as offline when heartbeat is stale."""
    while True:
        await asyncio.sleep(60)
        try:
            conn = psycopg2.connect(DATABASE_URL)
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE nodes
                       SET status = 'offline', updated_at = now()
                       WHERE status != 'offline'
                         AND last_heartbeat < now() - interval '%s seconds'""",
                    (HEARTBEAT_STALE_SECONDS,),
                )
                if cur.rowcount > 0:
                    logger.info("Heartbeat sweep: marked %d node(s) offline", cur.rowcount)
                    event_bus.publish("nodes")
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("Heartbeat sweep error: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    event_bus.set_loop(asyncio.get_running_loop())
    init_db()
    task = asyncio.create_task(_heartbeat_sweep())
    yield
    task.cancel()


app = FastAPI(title="Mission Control", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Auth ──────────────────────────────────────────────────────────────────────


def verify_api_key(x_api_key: Optional[str] = Header(None)):
    if not API_KEY:
        return
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ── Models ────────────────────────────────────────────────────────────────────


class TaskStatus(str, Enum):
    todo = "todo"
    in_progress = "in_progress"
    blocked = "blocked"
    review = "review"
    done = "done"


class TaskPriority(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    urgent = "urgent"


class TaskCreate(BaseModel):
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.todo
    priority: TaskPriority = TaskPriority.medium
    assignee: str = "enrico"
    project: str = ""
    tags: list[str] = Field(default_factory=list)
    due_date: Optional[datetime] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[TaskStatus] = None
    priority: Optional[TaskPriority] = None
    assignee: Optional[str] = None
    project: Optional[str] = None
    tags: Optional[list[str]] = None
    due_date: Optional[datetime] = None


class TaskResponse(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    status: str
    priority: str
    assignee: str
    project: str
    tags: list[str]
    due_date: Optional[datetime]
    created_at: datetime
    updated_at: datetime


def row_to_task(row: tuple, columns: list[str]) -> dict:
    return dict(zip(columns, row))


TASK_COLUMNS = [
    "id", "title", "description", "status", "priority",
    "assignee", "project", "tags", "due_date", "created_at", "updated_at",
]


# ── Node Models ──────────────────────────────────────────────────────────────


class NodeStatus(str, Enum):
    online = "online"
    healthy = "healthy"
    degraded = "degraded"
    offline = "offline"


class NodeCreate(BaseModel):
    name: str
    hostname: str
    hardware: str = ""
    framework: str = ""
    status: str = "offline"
    ram_total_mb: int = 0
    ram_used_mb: int = 0
    cpu_percent: float = 0
    last_heartbeat: Optional[datetime] = None
    metadata: dict = Field(default_factory=dict)


class NodeUpdate(BaseModel):
    hostname: Optional[str] = None
    hardware: Optional[str] = None
    framework: Optional[str] = None
    status: Optional[str] = None
    ram_total_mb: Optional[int] = None
    ram_used_mb: Optional[int] = None
    cpu_percent: Optional[float] = None
    last_heartbeat: Optional[datetime] = None
    metadata: Optional[dict] = None


class NodeResponse(BaseModel):
    id: uuid.UUID
    name: str
    hostname: str
    hardware: str
    framework: str
    status: str
    ram_total_mb: int
    ram_used_mb: int
    cpu_percent: float
    last_heartbeat: Optional[datetime]
    metadata: dict
    created_at: datetime
    updated_at: datetime


NODE_COLUMNS = [
    "id", "name", "hostname", "hardware", "framework", "status",
    "ram_total_mb", "ram_used_mb", "cpu_percent", "last_heartbeat",
    "metadata", "created_at", "updated_at",
]


# ── Service Monitoring Models ────────────────────────────────────────────────


class ServiceCheckIn(BaseModel):
    service: str
    status: str  # 'up', 'degraded', 'down'
    response_ms: Optional[int] = None
    error: Optional[str] = None
    checked_at: Optional[datetime] = None


class ServiceCheckBulk(BaseModel):
    checks: list[ServiceCheckIn]


class ServiceAlertIn(BaseModel):
    service: str
    status: str  # 'down', 'degraded', 'recovered'
    message: Optional[str] = None
    downtime_seconds: Optional[int] = None


# ── Routes ────────────────────────────────────────────────────────────────────


@app.get("/health")
def health():
    return {"status": "ok"}




@app.get("/api/events")
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

@app.get("/api/tasks", response_model=list[TaskResponse])
def list_tasks(
    status: Optional[str] = Query(None),
    assignee: Optional[str] = Query(None),
    project: Optional[str] = Query(None),
    conn=Depends(get_db),
):
    clauses = []
    params = []
    if status:
        clauses.append("status = %s")
        params.append(status)
    if assignee:
        clauses.append("assignee = %s")
        params.append(assignee)
    if project:
        clauses.append("project = %s")
        params.append(project)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = f"SELECT {', '.join(TASK_COLUMNS)} FROM tasks {where} ORDER BY created_at DESC"

    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    return [row_to_task(r, TASK_COLUMNS) for r in rows]


@app.get("/api/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: uuid.UUID, conn=Depends(get_db)):
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(TASK_COLUMNS)} FROM tasks WHERE id = %s",
            (task_id,),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    return row_to_task(row, TASK_COLUMNS)


@app.post("/api/tasks", response_model=TaskResponse, status_code=201)
def create_task(task: TaskCreate, conn=Depends(get_db), _=Depends(verify_api_key)):
    with conn.cursor() as cur:
        cur.execute(
            f"""INSERT INTO tasks (title, description, status, priority, assignee, project, tags, due_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING {', '.join(TASK_COLUMNS)}""",
            (
                task.title, task.description, task.status.value,
                task.priority.value, task.assignee, task.project,
                task.tags, task.due_date,
            ),
        )
        row = cur.fetchone()
    conn.commit()
    event_bus.publish("tasks")
    return row_to_task(row, TASK_COLUMNS)


@app.patch("/api/tasks/{task_id}", response_model=TaskResponse)
def update_task(
    task_id: uuid.UUID, task: TaskUpdate, conn=Depends(get_db), _=Depends(verify_api_key)
):
    updates = {}
    data = task.model_dump(exclude_unset=True)
    for key, val in data.items():
        if val is not None:
            updates[key] = val.value if isinstance(val, Enum) else val

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates["updated_at"] = datetime.now(timezone.utc)
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [task_id]

    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE tasks SET {set_clause} WHERE id = %s RETURNING {', '.join(TASK_COLUMNS)}",
            values,
        )
        row = cur.fetchone()
    conn.commit()
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    event_bus.publish("tasks")
    return row_to_task(row, TASK_COLUMNS)


@app.delete("/api/tasks/{task_id}", status_code=204)
def delete_task(task_id: uuid.UUID, conn=Depends(get_db), _=Depends(verify_api_key)):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Task not found")
    conn.commit()
    event_bus.publish("tasks")


@app.get("/api/stats")
def stats(conn=Depends(get_db)):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status = 'todo') AS todo,
                COUNT(*) FILTER (WHERE status = 'in_progress') AS in_progress,
                COUNT(*) FILTER (WHERE status = 'blocked') AS blocked,
                COUNT(*) FILTER (WHERE status = 'review') AS review,
                COUNT(*) FILTER (WHERE status = 'done') AS done,
                COUNT(*) FILTER (WHERE assignee = 'enrico') AS enrico,
                COUNT(*) FILTER (WHERE assignee = 'maxwell') AS maxwell
            FROM tasks
        """)
        row = cur.fetchone()
    cols = ["total", "todo", "in_progress", "blocked", "review", "done", "enrico", "maxwell"]
    return dict(zip(cols, row))


# ── Node Routes ──────────────────────────────────────────────────────────────


@app.get("/api/nodes", response_model=list[NodeResponse])
def list_nodes(conn=Depends(get_db)):
    with conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(NODE_COLUMNS)} FROM nodes ORDER BY name")
        rows = cur.fetchall()
    return [row_to_task(r, NODE_COLUMNS) for r in rows]


@app.post("/api/nodes", response_model=NodeResponse, status_code=201)
def upsert_node(node: NodeCreate, conn=Depends(get_db), _=Depends(verify_api_key)):
    """Upsert a node — create if new, update if exists."""
    with conn.cursor() as cur:
        cur.execute(
            f"""INSERT INTO nodes (name, hostname, hardware, framework, status,
                    ram_total_mb, ram_used_mb, cpu_percent, last_heartbeat, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (name) DO UPDATE SET
                    hostname = EXCLUDED.hostname,
                    hardware = EXCLUDED.hardware,
                    framework = EXCLUDED.framework,
                    status = EXCLUDED.status,
                    ram_total_mb = EXCLUDED.ram_total_mb,
                    ram_used_mb = EXCLUDED.ram_used_mb,
                    cpu_percent = EXCLUDED.cpu_percent,
                    last_heartbeat = EXCLUDED.last_heartbeat,
                    metadata = EXCLUDED.metadata,
                    updated_at = now()
                RETURNING {', '.join(NODE_COLUMNS)}""",
            (
                node.name, node.hostname, node.hardware, node.framework,
                node.status, node.ram_total_mb, node.ram_used_mb,
                node.cpu_percent, node.last_heartbeat,
                json.dumps(node.metadata),
            ),
        )
        row = cur.fetchone()
    conn.commit()
    event_bus.publish("nodes")
    return row_to_task(row, NODE_COLUMNS)


@app.patch("/api/nodes/{name}", response_model=NodeResponse)
def update_node(name: str, node: NodeUpdate, conn=Depends(get_db), _=Depends(verify_api_key)):
    updates = {}
    data = node.model_dump(exclude_unset=True)
    for key, val in data.items():
        if val is not None:
            updates[key] = json.dumps(val) if key == "metadata" else val

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates["updated_at"] = datetime.now(timezone.utc)
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [name]

    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE nodes SET {set_clause} WHERE name = %s RETURNING {', '.join(NODE_COLUMNS)}",
            values,
        )
        row = cur.fetchone()
    conn.commit()
    if not row:
        raise HTTPException(status_code=404, detail="Node not found")
    event_bus.publish("nodes")
    return row_to_task(row, NODE_COLUMNS)


# ── Memory Endpoints ─────────────────────────────────────────────────────────


def _workspace_files():
    """Return workspace markdown files as memory items."""
    ws = OPENCLAW_DIR / "workspace"
    items = []
    if not ws.is_dir():
        return items
    for f in sorted(ws.iterdir()):
        if f.suffix == ".md" and f.is_file():
            try:
                text = f.read_text(errors="replace")
            except OSError:
                continue
            rel = f"workspace/{f.name}"
            items.append({
                "id": rel,
                "path": rel,
                "source": "workspace",
                "title": f.name,
                "text": text,
                "updated_at": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
            })
    return items


def _search_fts(q: str, limit: int, offset: int):
    """Search the SQLite FTS5 index for matching chunks."""
    db_path = OPENCLAW_DIR / "memory" / "main.sqlite"
    if not db_path.is_file():
        return [], 0
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        # Check if chunks_fts table exists
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chunks_fts'")
        if not cur.fetchone():
            conn.close()
            return [], 0
        # Count total matches
        cur.execute("SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH ?", (q,))
        total = cur.fetchone()[0]
        # Get paginated results with snippets
        cur.execute(
            """SELECT c.id, c.source, c.metadata, snippet(chunks_fts, 0, '<mark>', '</mark>', '...', 40) AS snippet,
                      c.content
               FROM chunks_fts fts
               JOIN chunks c ON c.id = fts.rowid
               WHERE chunks_fts MATCH ?
               ORDER BY rank
               LIMIT ? OFFSET ?""",
            (q, limit, offset),
        )
        rows = cur.fetchall()
        items = []
        for r in rows:
            items.append({
                "id": f"chunk-{r['id']}",
                "path": r["source"] or "",
                "source": "memory",
                "title": r["source"] or f"chunk-{r['id']}",
                "text": r["content"] or "",
                "snippet": r["snippet"],
            })
        conn.close()
        return items, total
    except Exception:
        return [], 0


@app.get("/api/memories")
def list_memories(
    q: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    ws_items = _workspace_files()

    if q:
        # Filter workspace files by search term
        q_lower = q.lower()
        ws_matched = [it for it in ws_items if q_lower in it["text"].lower() or q_lower in it["title"].lower()]
        fts_items, fts_total = _search_fts(q, limit, offset)
        # Combine: workspace matches first, then FTS results
        all_items = ws_matched + fts_items
        total = len(ws_matched) + fts_total
    else:
        all_items = ws_items
        total = len(ws_items)

    # Apply pagination to combined results (workspace files are few so include all)
    paginated = all_items[offset:offset + limit] if not q else all_items[:limit]
    return {"items": paginated, "total": total, "limit": limit, "offset": offset}


@app.get("/api/memories/file")
def get_memory_file(path: str = Query(...)):
    # Path traversal protection
    resolved = (OPENCLAW_DIR / path).resolve()
    if not str(resolved).startswith(str(OPENCLAW_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        content = resolved.read_text(errors="replace")
    except OSError:
        raise HTTPException(status_code=500, detail="Cannot read file")
    return {"path": path, "content": content}


# ── Team Endpoint ─────────────────────────────────────────────────────────────

TEAM_ROSTER = {
    "orchestrator": {
        "name": "Maxwell",
        "emoji": "\U0001f9e0",
        "role": "Chief of Staff",
        "description": "Orchestrates all subagent teams and coordinates task execution",
    },
    "teams": [
        {
            "name": "Engineering",
            "members": [
                {"name": "Archie", "emoji": "\U0001f3d7\ufe0f", "role": "Backend Developer", "description": "APIs, databases, server architecture"},
                {"name": "Pixel", "emoji": "\U0001f3a8", "role": "Frontend Developer", "description": "UI, layouts, client-side logic"},
                {"name": "Harbor", "emoji": "\U0001f433", "role": "DevOps Engineer", "description": "Docker, CI/CD, infrastructure"},
                {"name": "Sentinel", "emoji": "\U0001f6e1\ufe0f", "role": "Security Engineer", "description": "Threat assessment, hardening, audit"},
            ],
        },
        {
            "name": "Content",
            "members": [
                {"name": "Docsworth", "emoji": "\U0001f4dd", "role": "Technical Writer", "description": "Docs, READMEs, guides"},
                {"name": "Stratton", "emoji": "\U0001f4c8", "role": "Content Strategist", "description": "Content planning, SEO, audience"},
                {"name": "Quill", "emoji": "\u270d\ufe0f", "role": "Copywriter", "description": "Marketing copy, product descriptions"},
            ],
        },
        {
            "name": "Design",
            "members": [
                {"name": "Flux", "emoji": "\U0001f9e9", "role": "UI/UX Designer", "description": "User flows, wireframes, interactions"},
                {"name": "Chroma", "emoji": "\U0001f308", "role": "Visual Designer", "description": "Color, typography, visual systems"},
                {"name": "Sigil", "emoji": "\u2b50", "role": "Brand Designer", "description": "Identity, logos, style guides"},
            ],
        },
        {
            "name": "Research & Analysis",
            "members": [
                {"name": "Scout", "emoji": "\U0001f50d", "role": "Research Analyst", "description": "Market research, competitive analysis"},
                {"name": "Ledger", "emoji": "\U0001f4ca", "role": "Data Analyst", "description": "Metrics, dashboards, reporting"},
            ],
        },
    ],
}


@app.get("/api/team")
def get_team():
    return TEAM_ROSTER


# ── Service Monitoring Endpoints ──────────────────────────────────────────────


@app.post("/api/services/check", status_code=201)
def bulk_insert_checks(payload: ServiceCheckBulk, conn=Depends(get_db), _=Depends(verify_api_key)):
    with conn.cursor() as cur:
        for c in payload.checks:
            cur.execute(
                """INSERT INTO service_checks (service, status, response_ms, error, checked_at)
                   VALUES (%s, %s, %s, %s, COALESCE(%s, now()))""",
                (c.service, c.status, c.response_ms, c.error, c.checked_at),
            )
    conn.commit()
    event_bus.publish("services")
    return {"inserted": len(payload.checks)}


@app.get("/api/services")
def list_services(conn=Depends(get_db)):
    with conn.cursor() as cur:
        # Get latest check per service
        cur.execute("""
            SELECT DISTINCT ON (service) service, status, response_ms, checked_at
            FROM service_checks
            ORDER BY service, checked_at DESC
        """)
        latest = {r[0]: {"service": r[0], "current_status": r[1], "last_response_ms": r[2], "last_checked": r[3].isoformat() if r[3] else None} for r in cur.fetchall()}

        # Uptime calculations
        for svc in latest:
            for label, hours in [("uptime_24h", 24), ("uptime_7d", 168)]:
                cur.execute("""
                    SELECT COUNT(*) FILTER (WHERE status IN ('up', 'degraded')) AS ok,
                           COUNT(*) AS total
                    FROM service_checks
                    WHERE service = %s AND checked_at > now() - interval '%s hours'
                """, (svc, hours))
                row = cur.fetchone()
                latest[svc][label] = round(row[0] / row[1] * 100, 1) if row[1] > 0 else None

        # Last incident per service
        for svc in latest:
            cur.execute("""
                SELECT created_at FROM service_alerts
                WHERE service = %s ORDER BY created_at DESC LIMIT 1
            """, (svc,))
            row = cur.fetchone()
            latest[svc]["last_incident"] = row[0].isoformat() if row else None

    return list(latest.values())


@app.get("/api/services/alerts")
def list_service_alerts(hours: int = Query(24, ge=1, le=168), conn=Depends(get_db)):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, service, status, message, downtime_seconds, created_at
            FROM service_alerts
            WHERE created_at > now() - interval '1 hour' * %s
            ORDER BY created_at DESC
        """, (hours,))
        cols = ["id", "service", "status", "message", "downtime_seconds", "created_at"]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            if r["created_at"]:
                r["created_at"] = r["created_at"].isoformat()
            r["id"] = str(r["id"])
    return rows


@app.post("/api/services/alert", status_code=201)
def record_alert(alert: ServiceAlertIn, conn=Depends(get_db), _=Depends(verify_api_key)):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO service_alerts (service, status, message, downtime_seconds)
               VALUES (%s, %s, %s, %s)
               RETURNING id, created_at""",
            (alert.service, alert.status, alert.message, alert.downtime_seconds),
        )
        row = cur.fetchone()
    conn.commit()
    event_bus.publish("services")
    return {"id": str(row[0]), "created_at": row[1].isoformat()}


@app.post("/api/services/check/trigger")
async def trigger_smoke_test(_=Depends(verify_api_key)):
    global _last_smoke_trigger
    now = time.monotonic()
    if now - _last_smoke_trigger < 60:
        raise HTTPException(status_code=429, detail="Rate limited — max 1 trigger per 60 seconds")
    _last_smoke_trigger = now
    try:
        proc = await asyncio.create_subprocess_exec(
            "/usr/local/bin/system-smoke-test.sh", "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        import json as _json
        try:
            result = _json.loads(stdout.decode())
        except Exception:
            result = {"raw": stdout.decode(), "stderr": stderr.decode()}
        event_bus.publish("services")
        return result
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Smoke test timed out after 30s")
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="Smoke test script not found")


@app.get("/api/services/{name}/history")
def service_history(name: str, hours: int = Query(24, ge=1, le=168), conn=Depends(get_db)):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT service, status, response_ms, error, checked_at
            FROM service_checks
            WHERE service = %s AND checked_at > now() - interval '1 hour' * %s
            ORDER BY checked_at ASC
        """, (name, hours))
        cols = ["service", "status", "response_ms", "error", "checked_at"]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            if r["checked_at"]:
                r["checked_at"] = r["checked_at"].isoformat()
    return rows


# ── Dispatch ─────────────────────────────────────────────────────────────────

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

# Map personas → (node, delegate_agent, system_prompt)
PERSONA_ROUTING = {
    # Engineering → slave0 (Claude Sonnet)
    "Archie":   ("slave0", "coder",     "You are Archie, a backend developer. Focus on APIs, databases, and server architecture."),
    "Pixel":    ("slave0", "coder",     "You are Pixel, a frontend developer. Focus on UI, layouts, and client-side logic."),
    "Harbor":   ("slave0", "coder",     "You are Harbor, a DevOps engineer. Focus on Docker, CI/CD, and infrastructure."),
    "Sentinel": ("slave0", "architect", "You are Sentinel, a security engineer. Focus on threat assessment, hardening, and audit."),
    # Content → slave1 (DeepSeek)
    "Docsworth": ("slave1", "assistant", "You are Docsworth, a technical writer. Focus on docs, READMEs, and guides."),
    "Stratton":  ("slave1", "assistant", "You are Stratton, a content strategist. Focus on content planning, SEO, and audience."),
    "Quill":     ("slave1", "assistant", "You are Quill, a copywriter. Focus on marketing copy and product descriptions."),
    # Design → slave1 (DeepSeek)
    "Flux":   ("slave1", "assistant", "You are Flux, a UI/UX designer. Focus on user flows, wireframes, and interactions."),
    "Chroma": ("slave1", "assistant", "You are Chroma, a visual designer. Focus on color, typography, and visual systems."),
    "Sigil":  ("slave1", "assistant", "You are Sigil, a brand designer. Focus on identity, logos, and style guides."),
    # Research → slave1 (DeepSeek)
    "Scout":  ("slave1", "assistant", "You are Scout, a research analyst. Focus on market research and competitive analysis."),
    "Ledger": ("slave1", "assistant", "You are Ledger, a data analyst. Focus on metrics, dashboards, and reporting."),
}


# ── Failover Config ──────────────────────────────────────────────────────────

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


class DispatchRequest(BaseModel):
    persona: str
    prompt: str
    timeout: int = Field(default=60, ge=5, le=300)


class DispatchResponse(BaseModel):
    persona: str
    node: str
    delegate: str
    response: str
    elapsed_ms: int
    fallback: bool = False
    original_node: Optional[str] = None


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
    try:
        conn = psycopg2.connect(DATABASE_URL)
        with conn.cursor() as cur:
            cur.execute(
                """SELECT status, last_heartbeat FROM nodes WHERE name = %s""",
                (node_name,),
            )
            row = cur.fetchone()
        conn.close()
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


def _log_dispatch(persona: str, node: str, delegate: str, fallback: bool,
                  prompt: str, response: str, elapsed_ms: int,
                  status: str = "success", error_detail: Optional[str] = None,
                  original_node: Optional[str] = None):
    """Log a dispatch attempt to the database."""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO dispatch_log
                   (persona, node, delegate, fallback, original_node, prompt_preview,
                    response_preview, elapsed_ms, status, error_detail)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (persona, node, delegate, fallback, original_node,
                 prompt[:500], response[:500],
                 elapsed_ms, status, error_detail),
            )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("Failed to log dispatch: %s", e)


@app.post("/api/dispatch", response_model=DispatchResponse)
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
                          0, "error", "All nodes unavailable", original_node=None)
            raise HTTPException(status_code=503, detail="All dispatch nodes are unavailable")

    cfg = ZEROCLAW_NODES[node]
    if not cfg["token"]:
        raise HTTPException(status_code=503, detail=f"Node {node} not configured (missing token)")

    # Rate limiting
    try:
        rate_limiter.check(node)
    except HTTPException as rate_exc:
        _log_dispatch(req.persona, node, delegate, is_fallback, req.prompt, "",
                      0, "rate_limited", rate_exc.detail, original_node=original_node)
        raise

    start = datetime.now(timezone.utc)
    try:
        response = await _zeroclaw_chat(node, req.prompt, system_prompt, req.timeout)
    except websockets.exceptions.WebSocketException as e:
        elapsed_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        logger.error("Dispatch to %s failed: %s", node, e)
        _log_dispatch(req.persona, node, delegate, is_fallback, req.prompt, "",
                      elapsed_ms, "error", str(e), original_node=original_node)
        raise HTTPException(status_code=502, detail=f"Cannot reach {node}: {e}")
    except HTTPException as e:
        elapsed_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        _log_dispatch(req.persona, node, delegate, is_fallback, req.prompt, "",
                      elapsed_ms, "error", e.detail, original_node=original_node)
        raise

    elapsed_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
    _log_dispatch(req.persona, node, delegate, is_fallback, req.prompt, response,
                  elapsed_ms, original_node=original_node)

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


@app.get("/api/dispatch/personas")
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


class DispatchLogEntry(BaseModel):
    persona: str = ""
    node: str = ""
    delegate: str = ""
    fallback: bool = False
    prompt_preview: str = ""
    response_preview: str = ""
    elapsed_ms: int = 0
    status: str = "success"
    error_detail: Optional[str] = None


@app.post("/api/dispatch/log", status_code=201)
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


@app.get("/api/dispatch/log")
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


# ── Budget / Cost Tracking Endpoints ──────────────────────────────────────────

OPENROUTER_API_KEY = os.environ.get(
    "OPENROUTER_API_KEY",
    "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",
)
BUDGET_DAILY = float(os.environ.get("BUDGET_DAILY", "5.00"))
BUDGET_WEEKLY = float(os.environ.get("BUDGET_WEEKLY", "25.00"))
BUDGET_MONTHLY = float(os.environ.get("BUDGET_MONTHLY", "75.00"))
BUDGET_ALERT_THRESHOLD = float(os.environ.get("BUDGET_ALERT_THRESHOLD", "0.80"))

_budget_cache: tuple[float, dict] = (0, {})


def _fetch_openrouter_usage() -> dict:
    """Fetch usage from OpenRouter API (cached 5 min)."""
    global _budget_cache
    now = time.time()
    if now - _budget_cache[0] < 300 and _budget_cache[1]:
        return _budget_cache[1]
    try:
        from urllib.request import Request as UrlReq, urlopen as urlopen_
        req = UrlReq(
            "https://openrouter.ai/api/v1/auth/key",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
        )
        resp = urlopen_(req, timeout=10)
        data = json.loads(resp.read().decode())["data"]
        result = {
            "daily_usd": data.get("usage_daily", 0),
            "weekly_usd": data.get("usage_weekly", 0),
            "monthly_usd": data.get("usage_monthly", 0),
            "total_usd": data.get("usage", 0),
        }
        _budget_cache = (now, result)
        return result
    except Exception as e:
        logger.warning("OpenRouter usage fetch failed: %s", e)
        return _budget_cache[1] if _budget_cache[1] else {"error": str(e)}


@app.get("/api/budget")
def get_budget():
    """Current OpenRouter spend vs budget limits."""
    usage = _fetch_openrouter_usage()
    if "error" in usage:
        raise HTTPException(503, detail=usage["error"])
    return {
        "usage": usage,
        "limits": {"daily": BUDGET_DAILY, "weekly": BUDGET_WEEKLY, "monthly": BUDGET_MONTHLY},
        "remaining": {
            "daily": round(BUDGET_DAILY - usage["daily_usd"], 2),
            "weekly": round(BUDGET_WEEKLY - usage["weekly_usd"], 2),
            "monthly": round(BUDGET_MONTHLY - usage["monthly_usd"], 2),
        },
        "alerts": {
            "daily": usage["daily_usd"] >= BUDGET_DAILY * BUDGET_ALERT_THRESHOLD,
            "weekly": usage["weekly_usd"] >= BUDGET_WEEKLY * BUDGET_ALERT_THRESHOLD,
            "monthly": usage["monthly_usd"] >= BUDGET_MONTHLY * BUDGET_ALERT_THRESHOLD,
        },
    }


# ── Trading Dashboard Endpoints ──────────────────────────────────────────────

_trading_cache: dict[str, tuple[float, any]] = {}
TRADING_CACHE_TTL = 30  # seconds


def _read_json_cached(filename: str, subdir: str = "") -> any:
    """Read a JSON file from polybot-data with 30s in-memory cache."""
    key = f"{subdir}/{filename}" if subdir else filename
    now = time.time()
    cached = _trading_cache.get(key)
    if cached and now - cached[0] < TRADING_CACHE_TTL:
        return cached[1]
    path = POLYBOT_DATA / subdir / filename if subdir else POLYBOT_DATA / filename
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
        _trading_cache[key] = (now, data)
        return data
    except Exception as e:
        logger.warning("Failed to read %s: %s", path, e)
        return None


def _compute_copybot_stats(control, positions, trades):
    """Compute copybot summary statistics."""
    positions = positions or []
    trades = trades or []
    control = control or {}

    total_unrealized = sum(
        (p.get("current_price", 0) - p.get("entry_price", 0)) * p.get("size", 0)
        for p in positions if not p.get("resolved")
    )

    executed = [t for t in trades if t.get("executed")]
    resolved = [t for t in executed if t.get("paper_result") in ("WIN", "LOSS")]
    wins = sum(1 for t in resolved if t["paper_result"] == "WIN")
    win_rate = round(wins / len(resolved) * 100, 1) if resolved else 0

    return {
        "mode": control.get("mode", "unknown"),
        "order_size_usd": round(control.get("order_size_usd", 0), 2),
        "daily_budget_usd": control.get("daily_budget_usd", 0),
        "daily_spent_usd": round(control.get("daily_spent_usd", 0), 2),
        "daily_date": control.get("daily_date", ""),
        "stop_loss_balance_usd": control.get("stop_loss_balance_usd", 0),
        "initial_balance_usd": control.get("initial_balance_usd", 0),
        "max_total_positions": control.get("max_total_positions", 0),
        "enabled_traders": control.get("enabled_traders", []),
        "position_count": len([p for p in positions if not p.get("resolved")]),
        "total_positions": len(positions),
        "unrealized_pnl": round(total_unrealized, 2),
        "total_trades": len(executed),
        "resolved_trades": len(resolved),
        "wins": wins,
        "losses": len(resolved) - wins,
        "win_rate": win_rate,
    }


def _compute_spreadbot_stats(spread_control, spread_state):
    """Compute spreadbot summary statistics."""
    sc = spread_control or {}
    pairs = spread_state or []

    state_counts = {}
    for p in pairs:
        s = p.get("state", "UNKNOWN")
        state_counts[s] = state_counts.get(s, 0) + 1

    settled_pnl = sum(p.get("pnl", 0) for p in pairs if p.get("state") in ("SETTLED", "CANCELLED"))
    active_exposure = sum(
        p.get("cost_usd", 0) for p in pairs if p.get("state") in ("PENDING", "PARTIAL", "LOCKED")
    )

    return {
        "mode": sc.get("mode", "unknown"),
        "order_size_usd": sc.get("order_size_usd", 0),
        "daily_budget_usd": sc.get("daily_budget_usd", 0),
        "daily_spent_usd": round(sc.get("daily_spent_usd", 0), 2),
        "max_pairs": sc.get("max_pairs", 0),
        "max_exposure_usd": sc.get("max_exposure_usd", 0),
        "state_counts": state_counts,
        "total_pairs": len(pairs),
        "settled_pnl": round(settled_pnl, 2),
        "active_exposure": round(active_exposure, 2),
        "base_spread": sc.get("base_spread", 0),
        "fee_free_only": sc.get("fee_free_only", True),
    }


def _compute_scalper_stats(spread_control, scalp_state):
    """Compute scalper summary statistics."""
    sc = spread_control or {}
    positions = scalp_state or []

    active = [p for p in positions if p.get("state") in ("PENDING", "OPEN")]
    closed = [p for p in positions if p.get("state") == "CLOSED"]

    total_pnl = sum(p.get("pnl", 0) for p in closed)
    wins = sum(1 for p in closed if p.get("pnl", 0) > 0)
    win_rate = round(wins / len(closed) * 100, 1) if closed else 0

    close_reasons = {}
    for p in closed:
        reason = p.get("close_reason", "unknown")
        close_reasons[reason] = close_reasons.get(reason, 0) + 1

    return {
        "enabled": sc.get("scalp_enabled", False),
        "mode": sc.get("mode", "unknown"),
        "max_concurrent": sc.get("max_concurrent_scalps", 5),
        "target_cents": sc.get("scalp_target_cents", 0.03),
        "stop_loss_cents": sc.get("scalp_stop_loss_cents", 0.03),
        "max_hold_minutes": sc.get("scalp_max_hold_minutes", 20),
        "daily_budget_usd": sc.get("scalp_daily_budget_usd", 0),
        "daily_spent_usd": round(sc.get("scalp_daily_spent_usd", 0), 2),
        "order_size_usd": sc.get("scalp_order_size_usd", 0),
        "active_count": len(active),
        "closed_count": len(closed),
        "total_pnl": round(total_pnl, 2),
        "wins": wins,
        "losses": len(closed) - wins,
        "win_rate": win_rate,
        "close_reasons": close_reasons,
        "exposure": sum(p.get("cost_usd", 0) for p in active),
    }


@app.get("/api/trading/overview")
def trading_overview():
    control = _read_json_cached("control.json")
    positions = _read_json_cached("positions.json")
    trades = _read_json_cached("paper_trades.json")
    spread_control = _read_json_cached("spread_control.json")
    spread_state = _read_json_cached("spread_state.json")
    scalp_state = _read_json_cached("scalp_state.json")

    cb = _compute_copybot_stats(control, positions, trades)
    sb = _compute_spreadbot_stats(spread_control, spread_state)
    sc = _compute_scalper_stats(spread_control, scalp_state)

    total_pnl = cb["unrealized_pnl"] + sb["settled_pnl"] + sc["total_pnl"]
    total_positions = cb["position_count"] + sb["state_counts"].get("LOCKED", 0) + sb["state_counts"].get("PARTIAL", 0) + sc["active_count"]

    # Recent activity: merge last trades from both bots
    recent = []
    for t in (trades or [])[-20:]:
        recent.append({
            "time": t.get("detected_at", ""),
            "bot": "copybot",
            "action": "COPY" if t.get("executed") else "SKIP",
            "detail": f"{t.get('trader', '?')} {t.get('side', '?')} on \"{t.get('market_title', '?')[:50]}\"",
            "result": t.get("paper_result"),
        })
    for p in (spread_state or [])[-20:]:
        recent.append({
            "time": p.get("updated_at", p.get("created_at", "")),
            "bot": "spreadbot",
            "action": p.get("state", "?"),
            "detail": f"\"{p.get('market_title', '?')[:50]}\"",
            "pnl": p.get("pnl"),
        })
    recent.sort(key=lambda x: x.get("time", ""), reverse=True)

    return {
        "total_pnl": round(total_pnl, 2),
        "total_positions": total_positions,
        "copybot": cb,
        "spreadbot": sb,
        "scalper": sc,
        "daily_budget_total": cb["daily_budget_usd"] + sb["daily_budget_usd"],
        "daily_spent_total": round(cb["daily_spent_usd"] + sb["daily_spent_usd"], 2),
        "recent_activity": recent[:20],
    }


@app.get("/api/trading/copybot/summary")
def copybot_summary():
    control = _read_json_cached("control.json")
    positions = _read_json_cached("positions.json")
    trades = _read_json_cached("paper_trades.json")
    return _compute_copybot_stats(control, positions, trades)


@app.get("/api/trading/copybot/positions")
def copybot_positions():
    positions = _read_json_cached("positions.json")
    if positions is None:
        return []
    for p in positions:
        entry = p.get("entry_price", 0)
        current = p.get("current_price", 0)
        size = p.get("size", 0)
        p["computed_pnl"] = round((current - entry) * size, 2)
    return positions


@app.get("/api/trading/copybot/trades")
def copybot_trades(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    trades = _read_json_cached("paper_trades.json")
    if trades is None:
        return {"items": [], "total": 0}
    # Reverse for most recent first
    trades_rev = list(reversed(trades))
    return {"items": trades_rev[offset:offset + limit], "total": len(trades)}


@app.get("/api/trading/copybot/traders")
def copybot_traders():
    trades = _read_json_cached("paper_trades.json")
    positions = _read_json_cached("positions.json")
    if not trades:
        return []

    trader_stats: dict[str, dict] = {}
    for t in trades:
        trader = t.get("trader", "unknown")
        if trader not in trader_stats:
            trader_stats[trader] = {"trader": trader, "total": 0, "executed": 0, "wins": 0, "losses": 0, "skipped": 0}
        s = trader_stats[trader]
        s["total"] += 1
        if t.get("executed"):
            s["executed"] += 1
            if t.get("paper_result") == "WIN":
                s["wins"] += 1
            elif t.get("paper_result") == "LOSS":
                s["losses"] += 1
        else:
            s["skipped"] += 1

    # Count active positions per trader
    if positions:
        for p in positions:
            # Infer trader from market - not directly in positions, so skip
            pass

    result = list(trader_stats.values())
    for s in result:
        resolved = s["wins"] + s["losses"]
        s["win_rate"] = round(s["wins"] / resolved * 100, 1) if resolved else 0
    result.sort(key=lambda x: x["executed"], reverse=True)
    return result


@app.get("/api/trading/spreadbot/summary")
def spreadbot_summary():
    spread_control = _read_json_cached("spread_control.json")
    spread_state = _read_json_cached("spread_state.json")
    return _compute_spreadbot_stats(spread_control, spread_state)


@app.get("/api/trading/spreadbot/pairs")
def spreadbot_pairs(
    state: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    pairs = _read_json_cached("spread_state.json")
    if pairs is None:
        return {"items": [], "total": 0}
    if state and state != "all":
        pairs = [p for p in pairs if p.get("state") == state.upper()]
    pairs_rev = list(reversed(pairs))
    return {"items": pairs_rev[offset:offset + limit], "total": len(pairs)}


@app.get("/api/trading/scalper/summary")
def scalper_summary():
    spread_control = _read_json_cached("spread_control.json")
    scalp_state = _read_json_cached("scalp_state.json")
    return _compute_scalper_stats(spread_control, scalp_state)


@app.get("/api/trading/scalper/positions")
def scalper_positions():
    positions = _read_json_cached("scalp_state.json")
    return positions if positions else []


@app.get("/api/trading/backtest")
def trading_backtest():
    report = _read_json_cached("backtest_report.json", subdir="backtest")
    leaderboard = _read_json_cached("leaderboard_backtest_report.json", subdir="backtest")
    control = _read_json_cached("control.json")
    enabled = control.get("enabled_traders", []) if control else []
    return {
        "report": report or [],
        "leaderboard": leaderboard or [],
        "enabled_traders": enabled,
    }
