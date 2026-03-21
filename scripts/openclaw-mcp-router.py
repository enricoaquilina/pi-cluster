#!/usr/bin/env python3
"""
OpenClaw Cluster Router API
HTTP API for task routing, node stats, and concurrency tracking.

Endpoints:
  GET  /health          - Cluster health (JSON)
  GET  /route/<type>    - Get best node for task type
  POST /dispatch        - Execute command on best node
  GET  /nodes           - Cached node stats
  POST /node-stats      - Receive pushed stats from node agents
  GET  /concurrency     - Active task counts per node
  POST /task/start      - Register task start (for concurrency tracking)
  POST /task/end        - Register task end

Runs on port 8520 on master.
"""

import json
import os
import subprocess
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

CACHE_FILE = "/tmp/openclaw-node-stats.json"
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
GATEWAY_CONTAINER = "openclaw-openclaw-gateway-1"
PORT = 8520

# Concurrency tracking
active_tasks = {}  # node_name -> count
active_tasks_lock = threading.Lock()

# Node config
MAX_CONCURRENT = {
    "control": 3,   # Lower than inventory — protect gateway
    "build": 5,
    "light": 3,
    "heavy": 10,
}

# RAM thresholds — control is lower to protect gateway
MAX_RAM = {
    "control": 50,
    "build": 85,
    "light": 80,
    "heavy": 90,
}


def get_cached_stats():
    """Read cached node stats."""
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
        age = time.time() - os.path.getmtime(CACHE_FILE)
        data["cache_age_seconds"] = round(age, 1)
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {"error": "no cache available", "nodes": []}


def update_node_stats(node_stats):
    """Update cached stats from a node agent push."""
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {"timestamp": "", "nodes": []}

    name = node_stats.get("name")
    if not name:
        return False

    # Update or add node
    updated = False
    for i, n in enumerate(data["nodes"]):
        if n["name"] == name:
            node_stats["reachable"] = True
            node_stats["connected"] = n.get("connected", False)
            node_stats["mc_name"] = n.get("mc_name", name)
            node_stats["host"] = n.get("host", name)
            data["nodes"][i] = node_stats
            updated = True
            break

    if not updated:
        node_stats["reachable"] = True
        node_stats["connected"] = False
        data["nodes"].append(node_stats)

    data["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Write atomically
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, CACHE_FILE)
    os.chmod(CACHE_FILE, 0o644)
    return True


def route_task(task_type):
    """Select best node using cached stats + concurrency limits."""
    roles = {"control": "orchestrator", "build": "coding", "light": "research", "heavy": "compute"}
    affinity = {
        "coding": ["build", "control", "heavy", "light"],
        "research": ["light", "control", "build", "heavy"],
        "compute": ["heavy", "build", "control", "light"],
        "orchestrator": ["control", "heavy", "build", "light"],
        "any": ["heavy", "control", "build", "light"],
    }

    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

    candidates = affinity.get(task_type, affinity["any"])
    best = None
    best_score = 999999

    with active_tasks_lock:
        current_tasks = dict(active_tasks)

    for node in data.get("nodes", []):
        name = node["name"]
        if name not in candidates:
            continue
        if not node.get("reachable") or not node.get("connected"):
            continue

        ram_pct = node.get("ram_pct", 100)
        if ram_pct > MAX_RAM.get(name, 85):
            continue

        # Check concurrency limit
        running = current_tasks.get(name, 0)
        if running >= MAX_CONCURRENT.get(name, 5):
            continue

        load = node.get("load", 99)
        score = ram_pct + int(load) * 10 + running * 20  # Penalize busy nodes
        if roles.get(name) == task_type:
            score -= 50

        if score < best_score:
            best_score = score
            best = name

    return best


def dispatch_command(task_type, command, cwd=None):
    """Route and execute a command on the best node with concurrency tracking."""
    node = route_task(task_type)
    if not node:
        return {"error": f"no suitable node for '{task_type}' (all busy or overloaded)", "node": None}

    # Track task start
    with active_tasks_lock:
        active_tasks[node] = active_tasks.get(node, 0) + 1

    try:
        cmd = ["docker", "exec", GATEWAY_CONTAINER, "openclaw", "nodes", "run",
               "--node", node, "--raw", command]
        if cwd:
            cmd.extend(["--cwd", cwd])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        output = "\n".join(
            line for line in result.stdout.splitlines()
            if "plugin" not in line.lower() and "Config warnings" not in line
            and not line.startswith("│") and not line.startswith("├")
            and not line.startswith("◇") and line.strip()
        )
        return {
            "node": node, "task_type": task_type,
            "output": output, "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": "command timed out", "node": node}
    finally:
        # Track task end
        with active_tasks_lock:
            active_tasks[node] = max(0, active_tasks.get(node, 1) - 1)


class RouterHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health" or self.path == "/nodes":
            self.send_json(get_cached_stats())
        elif self.path.startswith("/route/"):
            task_type = self.path.split("/route/")[1]
            node = route_task(task_type)
            if node:
                self.send_json({"task_type": task_type, "node": node})
            else:
                self.send_json({"task_type": task_type, "node": None,
                                "error": "no suitable node"}, 503)
        elif self.path == "/concurrency":
            with active_tasks_lock:
                tasks = dict(active_tasks)
            self.send_json({
                "active_tasks": tasks,
                "limits": MAX_CONCURRENT,
            })
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if self.path == "/dispatch":
            task_type = body.get("task_type", "any")
            command = body.get("command")
            cwd = body.get("cwd")
            if not command:
                self.send_json({"error": "command required"}, 400)
                return
            result = dispatch_command(task_type, command, cwd)
            status = 200 if "error" not in result else 503
            self.send_json(result, status)

        elif self.path == "/node-stats":
            if update_node_stats(body):
                self.send_json({"ok": True})
            else:
                self.send_json({"error": "invalid stats"}, 400)

        elif self.path == "/task/start":
            node = body.get("node")
            if node:
                with active_tasks_lock:
                    active_tasks[node] = active_tasks.get(node, 0) + 1
                self.send_json({"ok": True, "active": active_tasks.get(node, 0)})
            else:
                self.send_json({"error": "node required"}, 400)

        elif self.path == "/task/end":
            node = body.get("node")
            if node:
                with active_tasks_lock:
                    active_tasks[node] = max(0, active_tasks.get(node, 1) - 1)
                self.send_json({"ok": True, "active": active_tasks.get(node, 0)})
            else:
                self.send_json({"error": "node required"}, 400)

        else:
            self.send_json({"error": "not found"}, 404)


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), RouterHandler)
    print(f"OpenClaw Router API listening on port {PORT}")
    server.serve_forever()
