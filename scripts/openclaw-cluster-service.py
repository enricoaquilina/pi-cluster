#!/usr/bin/env python3
"""
OpenClaw Cluster Service - FastAPI app for routing, dispatch, stats, and logging.

Consolidates: router, dispatcher, stats collector, MC feed, dispatch logging.

Endpoints:
  GET  /health              - Cluster health
  GET  /nodes               - Node stats
  GET  /route/{task_type}   - Best node + model
  POST /dispatch            - Execute on best node (logged)
  POST /node-stats          - Receive push from node agents
  GET  /concurrency         - Active tasks per node
  GET  /dispatch-log        - Recent dispatch history
  GET  /dispatch-log/stats  - Dispatch statistics
  GET  /docs                - OpenAPI docs (auto-generated)

Run: uvicorn scripts.openclaw-cluster-service:app --host 0.0.0.0 --port 8520
"""

import asyncio
import json
import logging
import os
import subprocess
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen

import aiosqlite
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# ── Config ───────────────────────────────────────────────────────────────────

CACHE_FILE = "/tmp/openclaw-node-stats.json"
LOG_DB = "/tmp/openclaw-dispatch-log.db"
GATEWAY_CONTAINER = "openclaw-openclaw-gateway-1"
MC_API = "http://127.0.0.1:8000/api"
MC_KEY = os.environ.get("MC_API_KEY", "860e75126051c283758226e6852fcb687b1423c2b7c0af51")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# Budget limits (USD)
BUDGET_DAILY = 5.00
BUDGET_WEEKLY = 25.00
BUDGET_MONTHLY = 75.00
BUDGET_ALERT_THRESHOLD = 0.80  # Alert at 80%

# Telegram alerting
TELEGRAM_BOT_TOKEN = ""  # Read from watchdog script at startup
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "1630148884")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cluster")

# ── Node Definitions ─────────────────────────────────────────────────────────

MAX_CONCURRENT = {"control": 3, "build": 5, "light": 3, "heavy": 10}
MAX_RAM = {"control": 50, "build": 85, "light": 80, "heavy": 90}
ROLES = {"control": "orchestrator", "build": "coding", "light": "research", "heavy": "compute"}
MC_NAMES = {"control": "master", "build": "slave0", "light": "slave1", "heavy": "heavy"}

AFFINITY = {
    "coding": ["build", "control", "heavy", "light"],
    "research": ["light", "control", "build", "heavy"],
    "compute": ["heavy", "build", "control", "light"],
    "orchestrator": ["control", "heavy", "build", "light"],
    "any": ["heavy", "control", "build", "light"],
}

MODEL_CONFIG = {
    "research": {"primary": "openrouter/qwen/qwen3.5-plus-02-15",
                 "fallbacks": ["openrouter/minimax/minimax-m2.7", "openrouter/google/gemini-2.5-flash-lite"],
                 "tier": "economy"},
    "coding": {"primary": "openrouter/anthropic/claude-sonnet-4.6",
               "fallbacks": ["openrouter/z-ai/glm-5", "openrouter/minimax/minimax-m2.7"],
               "tier": "standard"},
    "compute": {"primary": "openrouter/anthropic/claude-opus-4.6",
                "fallbacks": ["openrouter/openai/gpt-5.4", "openrouter/z-ai/glm-5"],
                "tier": "premium"},
    "any": {"primary": "openrouter/minimax/minimax-m2.7",
            "fallbacks": ["openrouter/google/gemini-2.5-flash", "openrouter/deepseek/deepseek-v3.2"],
            "tier": "standard"},
}


# ── Pydantic Models ──────────────────────────────────────────────────────────

class TaskType(str, Enum):
    coding = "coding"
    research = "research"
    compute = "compute"
    orchestrator = "orchestrator"
    any = "any"


class DispatchRequest(BaseModel):
    task_type: TaskType = TaskType.any
    command: str
    cwd: Optional[str] = None


class NodeStats(BaseModel):
    name: str
    ram_total_mb: int = 0
    ram_used_mb: int = 0
    ram_avail_mb: int = 0
    ram_pct: int = 0
    swap_total_mb: int = 0
    swap_used_mb: int = 0
    load: float = 0.0
    cpus: int = 1
    arch: str = ""
    disk_total_mb: int = 0
    disk_used_mb: int = 0
    disk_avail_mb: int = 0
    disk_pct: int = 0
    temp_c: int = 0
    uptime_s: int = 0


class TaskNode(BaseModel):
    node: str


# ── State ────────────────────────────────────────────────────────────────────

active_tasks: dict[str, int] = {}
active_lock = asyncio.Lock()


# ── DB ───────────────────────────────────────────────────────────────────────

async def init_db():
    async with aiosqlite.connect(LOG_DB) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS dispatch_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                task_type TEXT NOT NULL,
                node TEXT,
                model TEXT,
                command TEXT,
                duration_ms INTEGER,
                exit_code INTEGER,
                success INTEGER,
                output_preview TEXT,
                error TEXT
            )
        """)
        await db.commit()


async def log_dispatch(entry: dict):
    try:
        async with aiosqlite.connect(LOG_DB) as db:
            await db.execute(
                """INSERT INTO dispatch_log
                   (timestamp, task_type, node, model, command, duration_ms, exit_code, success, output_preview, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (entry["timestamp"], entry["task_type"], entry.get("node"),
                 entry.get("model"), (entry.get("command") or "")[:200],
                 entry.get("duration_ms"), entry.get("exit_code"),
                 1 if entry.get("success") else 0,
                 (entry.get("output") or "")[:500], entry.get("error")),
            )
            await db.commit()
    except Exception as e:
        log.error(f"Log dispatch failed: {e}")


# ── Cache ────────────────────────────────────────────────────────────────────

def read_cache() -> dict:
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
        data["cache_age_seconds"] = round(time.time() - os.path.getmtime(CACHE_FILE), 1)
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {"error": "no cache", "nodes": []}


def write_cache(data: dict):
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, CACHE_FILE)
    os.chmod(CACHE_FILE, 0o644)


def update_node_in_cache(stats: dict) -> bool:
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {"timestamp": "", "nodes": []}

    name = stats.get("name")
    if not name:
        return False

    stats["reachable"] = True
    stats["mc_name"] = MC_NAMES.get(name, name)

    updated = False
    for i, n in enumerate(data["nodes"]):
        if n["name"] == name:
            stats["connected"] = n.get("connected", False)
            stats["host"] = n.get("host", name)
            data["nodes"][i] = stats
            updated = True
            break

    if not updated:
        stats["connected"] = False
        stats["host"] = name
        data["nodes"].append(stats)

    data["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    write_cache(data)
    return True


def push_to_mc(stats: dict):
    """Push to Mission Control (sync, best-effort)."""
    mc_name = stats.get("mc_name", MC_NAMES.get(stats["name"], stats["name"]))
    payload = json.dumps({
        "name": mc_name,
        "framework": "OpenClaw",
        "status": "healthy" if stats.get("connected") else "degraded",
        "ram_total_mb": stats.get("ram_total_mb", 0),
        "ram_used_mb": stats.get("ram_used_mb", 0),
        "cpu_percent": stats.get("load", 0),
        "metadata": {k: stats.get(k, 0) for k in [
            "arch", "cpus", "ram_pct", "disk_total_mb", "disk_used_mb",
            "disk_avail_mb", "disk_pct", "temp_c", "uptime_s",
            "swap_total_mb", "swap_used_mb", "connected",
        ]},
    }).encode()
    for method in ["PATCH", "POST"]:
        path = f"/nodes/{mc_name}" if method == "PATCH" else "/nodes"
        try:
            req = Request(MC_API + path, data=payload, method=method,
                          headers={"Content-Type": "application/json", "X-Api-Key": MC_KEY})
            urlopen(req, timeout=5)
            break
        except Exception:
            continue


# ── Routing ──────────────────────────────────────────────────────────────────

def route_task(task_type: str) -> Optional[str]:
    data = read_cache()
    candidates = AFFINITY.get(task_type, AFFINITY["any"])
    best, best_score = None, 999999

    for node in data.get("nodes", []):
        name = node["name"]
        if name not in candidates or not node.get("reachable") or not node.get("connected"):
            continue
        ram_pct = node.get("ram_pct", 100)
        if ram_pct > MAX_RAM.get(name, 85):
            continue
        running = active_tasks.get(name, 0)
        if running >= MAX_CONCURRENT.get(name, 5):
            continue

        score = ram_pct + int(node.get("load", 99)) * 10 + running * 20
        if ROLES.get(name) == task_type:
            score -= 50
        if score < best_score:
            best_score, best = score, name

    return best


# ── Dispatch ─────────────────────────────────────────────────────────────────

async def execute_dispatch(task_type: str, command: str, cwd: Optional[str] = None) -> dict:
    node = route_task(task_type)
    model = MODEL_CONFIG.get(task_type, MODEL_CONFIG["any"])

    if not node:
        await log_dispatch({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task_type": task_type, "command": command,
            "success": False, "error": "no suitable node",
        })
        return {"error": "no suitable node (all busy or overloaded)", "node": None}

    async with active_lock:
        active_tasks[node] = active_tasks.get(node, 0) + 1

    start = time.time()
    try:
        cmd = ["docker", "exec", GATEWAY_CONTAINER, "openclaw", "nodes", "run",
               "--node", node, "--raw", command]
        if cwd:
            cmd.extend(["--cwd", cwd])

        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)

        output = "\n".join(
            line for line in stdout.decode().splitlines()
            if "plugin" not in line.lower() and "Config warnings" not in line
            and not line.startswith("│") and not line.startswith("├")
            and not line.startswith("◇") and line.strip()
        )
        duration_ms = int((time.time() - start) * 1000)
        success = proc.returncode == 0

        await log_dispatch({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task_type": task_type, "node": node, "model": model["primary"],
            "command": command, "duration_ms": duration_ms,
            "exit_code": proc.returncode, "success": success, "output": output,
        })
        log.info(f"DISPATCH {task_type}→{node} {duration_ms}ms {'OK' if success else 'FAIL'}")

        return {"node": node, "task_type": task_type, "model": model["primary"],
                "output": output, "exit_code": proc.returncode, "duration_ms": duration_ms}

    except asyncio.TimeoutError:
        duration_ms = int((time.time() - start) * 1000)
        await log_dispatch({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task_type": task_type, "node": node, "model": model["primary"],
            "command": command, "duration_ms": duration_ms,
            "success": False, "error": "timeout",
        })
        return {"error": "command timed out", "node": node}
    finally:
        async with active_lock:
            active_tasks[node] = max(0, active_tasks.get(node, 1) - 1)


# ── App ──────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    log.info("Cluster Service started on port 8520")
    yield

app = FastAPI(
    title="OpenClaw Cluster Service",
    description="Routing, dispatch, stats, and logging for the Pi cluster",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
@app.get("/nodes")
def get_nodes():
    """Cluster health and node stats."""
    return read_cache()


@app.get("/route/{task_type}")
def get_route(task_type: TaskType):
    """Select best node + model for a task type."""
    node = route_task(task_type.value)
    model = MODEL_CONFIG.get(task_type.value, MODEL_CONFIG["any"])
    if not node:
        raise HTTPException(503, detail="no suitable node available")
    return {"task_type": task_type.value, "node": node,
            "model": model["primary"], "fallbacks": model["fallbacks"], "tier": model["tier"]}


@app.post("/dispatch")
async def post_dispatch(req: DispatchRequest):
    """Execute command on the best node for the task type. Logged."""
    result = await execute_dispatch(req.task_type.value, req.command, req.cwd)
    if "error" in result:
        raise HTTPException(503, detail=result)
    return result


@app.post("/node-stats")
def post_node_stats(stats: NodeStats):
    """Receive pushed stats from a node agent."""
    data = stats.model_dump()
    if update_node_in_cache(data):
        push_to_mc(data)
        return {"ok": True}
    raise HTTPException(400, detail="invalid stats")


@app.get("/concurrency")
def get_concurrency():
    """Active task counts per node."""
    return {"active_tasks": dict(active_tasks), "limits": MAX_CONCURRENT}


@app.post("/task/start")
async def post_task_start(req: TaskNode):
    """Register a task start for concurrency tracking."""
    async with active_lock:
        active_tasks[req.node] = active_tasks.get(req.node, 0) + 1
    return {"ok": True, "active": active_tasks.get(req.node, 0)}


@app.post("/task/end")
async def post_task_end(req: TaskNode):
    """Register a task end."""
    async with active_lock:
        active_tasks[req.node] = max(0, active_tasks.get(req.node, 1) - 1)
    return {"ok": True, "active": active_tasks.get(req.node, 0)}


@app.get("/dispatch-log")
async def get_dispatch_log(limit: int = 50):
    """Recent dispatch history."""
    async with aiosqlite.connect(LOG_DB) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM dispatch_log ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]


@app.get("/dispatch-log/stats")
async def get_dispatch_stats():
    """Dispatch statistics: success rate, avg duration, by node/type."""
    async with aiosqlite.connect(LOG_DB) as db:
        db.row_factory = aiosqlite.Row
        row = (await db.execute_fetchall(
            "SELECT COUNT(*) as total, SUM(success) as ok, AVG(duration_ms) as avg_ms FROM dispatch_log"))[0]
        total = row["total"]
        stats = {
            "total": total,
            "success_rate": round(row["ok"] / total * 100, 1) if total else 0,
            "avg_duration_ms": round(row["avg_ms"]) if row["avg_ms"] else 0,
        }
        rows = await db.execute_fetchall(
            "SELECT node, COUNT(*) c, SUM(success) ok, AVG(duration_ms) avg_ms FROM dispatch_log GROUP BY node")
        stats["by_node"] = {r["node"]: {"count": r["c"], "success_rate": round(r["ok"] / r["c"] * 100, 1), "avg_ms": round(r["avg_ms"] or 0)} for r in rows}
        rows = await db.execute_fetchall(
            "SELECT task_type, COUNT(*) c, SUM(success) ok FROM dispatch_log GROUP BY task_type")
        stats["by_task_type"] = {r["task_type"]: {"count": r["c"], "success_rate": round(r["ok"] / r["c"] * 100, 1)} for r in rows}
        return stats


# ── Budget Tracking ──────────────────────────────────────────────────────────

# Model costs per 1M tokens (input/output) from OpenRouter
MODEL_COSTS = {
    "openrouter/anthropic/claude-sonnet-4.6": {"input": 3.00, "output": 15.00},
    "openrouter/anthropic/claude-opus-4.6": {"input": 5.00, "output": 25.00},
    "openrouter/openai/gpt-5.4": {"input": 2.50, "output": 15.00},
    "openrouter/z-ai/glm-5": {"input": 0.72, "output": 2.30},
    "openrouter/qwen/qwen3.5-plus-02-15": {"input": 0.26, "output": 1.56},
    "openrouter/minimax/minimax-m2.7": {"input": 0.30, "output": 1.20},
    "openrouter/google/gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "openrouter/google/gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40},
    "openrouter/deepseek/deepseek-v3.2": {"input": 0.26, "output": 0.38},
}


def get_openrouter_usage() -> dict:
    """Fetch real usage data from OpenRouter API."""
    try:
        req = Request(
            "https://openrouter.ai/api/v1/auth/key",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
        )
        resp = urlopen(req, timeout=10)
        data = json.loads(resp.read().decode())["data"]
        return {
            "daily_usd": data.get("usage_daily", 0),
            "weekly_usd": data.get("usage_weekly", 0),
            "monthly_usd": data.get("usage_monthly", 0),
            "total_usd": data.get("usage", 0),
        }
    except Exception as e:
        log.error(f"OpenRouter usage fetch failed: {e}")
        return {"error": str(e)}


def _load_telegram_token():
    """Load Telegram bot token from watchdog script."""
    global TELEGRAM_BOT_TOKEN
    if TELEGRAM_BOT_TOKEN:
        return
    try:
        import re
        with open("/home/enrico/homelab/scripts/openclaw-watchdog-cluster.sh") as f:
            match = re.search(r'TELEGRAM_BOT_TOKEN="([^"]+)"', f.read())
        if match:
            TELEGRAM_BOT_TOKEN = match.group(1)
    except Exception:
        pass


def send_telegram(message: str):
    """Send a Telegram alert (best-effort)."""
    _load_telegram_token()
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        payload = json.dumps({
            "chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"
        }).encode()
        req = Request(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data=payload, method="POST",
            headers={"Content-Type": "application/json"},
        )
        urlopen(req, timeout=10)
    except Exception:
        pass


@app.get("/budget")
def get_budget():
    """Current OpenRouter spend vs budget limits."""
    usage = get_openrouter_usage()
    if "error" in usage:
        raise HTTPException(503, detail=usage["error"])

    return {
        "usage": usage,
        "limits": {
            "daily": BUDGET_DAILY,
            "weekly": BUDGET_WEEKLY,
            "monthly": BUDGET_MONTHLY,
        },
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
        "model_costs": MODEL_COSTS,
    }


@app.get("/budget/check")
def check_budget_alerts():
    """Check budget and send Telegram alert if threshold reached."""
    usage = get_openrouter_usage()
    if "error" in usage:
        return {"error": usage["error"]}

    alerts = []
    if usage["daily_usd"] >= BUDGET_DAILY * BUDGET_ALERT_THRESHOLD:
        alerts.append(f"Daily: ${usage['daily_usd']:.2f} / ${BUDGET_DAILY:.2f}")
    if usage["weekly_usd"] >= BUDGET_WEEKLY * BUDGET_ALERT_THRESHOLD:
        alerts.append(f"Weekly: ${usage['weekly_usd']:.2f} / ${BUDGET_WEEKLY:.2f}")
    if usage["monthly_usd"] >= BUDGET_MONTHLY * BUDGET_ALERT_THRESHOLD:
        alerts.append(f"Monthly: ${usage['monthly_usd']:.2f} / ${BUDGET_MONTHLY:.2f}")

    if alerts:
        msg = f"*OpenRouter Budget Alert*\n" + "\n".join(alerts)
        send_telegram(msg)
        return {"alerted": True, "alerts": alerts}

    return {"alerted": False, "usage": usage}


@app.get("/budget/digest")
async def get_daily_digest():
    """Daily digest: dispatches, success rate, spend, busiest node."""
    usage = get_openrouter_usage()
    stats = await get_dispatch_stats()

    digest = {
        "spend": usage,
        "dispatches": stats,
        "budget_remaining": {
            "daily": round(BUDGET_DAILY - usage.get("daily_usd", 0), 2),
            "monthly": round(BUDGET_MONTHLY - usage.get("monthly_usd", 0), 2),
        },
    }

    # Build Telegram message
    total = stats.get("total", 0)
    rate = stats.get("success_rate", 0)
    daily = usage.get("daily_usd", 0)
    monthly = usage.get("monthly_usd", 0)
    busiest = max(stats.get("by_node", {}).items(), key=lambda x: x[1]["count"], default=("none", {"count": 0}))

    msg = (
        f"*Daily Cluster Digest*\n"
        f"Dispatches: {total} ({rate}% success)\n"
        f"Busiest: {busiest[0]} ({busiest[1]['count']} tasks)\n"
        f"Spend today: ${daily:.2f} / ${BUDGET_DAILY:.2f}\n"
        f"Spend month: ${monthly:.2f} / ${BUDGET_MONTHLY:.2f}"
    )
    send_telegram(msg)

    digest["telegram_sent"] = True
    return digest


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8520, log_level="info")
