#!/usr/bin/env python3
"""
OpenClaw MCP Tool: Cluster Router
Exposes task routing and node dispatch as an MCP-compatible HTTP API.
Agents can call this to route tasks to the best available node.

Endpoints:
  GET  /health          - Cluster health (JSON)
  GET  /route/<type>    - Get best node for task type (coding/research/compute/any)
  POST /dispatch        - Execute command on best node for task type
    Body: {"task_type": "coding", "command": "git status", "cwd": "/opt/workspace"}
  GET  /nodes           - Cached node stats

Runs on port 8520 on master.
"""

import json
import os
import subprocess
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

CACHE_FILE = "/tmp/openclaw-node-stats.json"
SCRIPTS_DIR = Path(__file__).parent
GATEWAY_CONTAINER = "openclaw-openclaw-gateway-1"
PORT = 8520


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


def route_task(task_type):
    """Select best node for task type using the router script."""
    try:
        result = subprocess.run(
            [str(SCRIPTS_DIR / "openclaw-router.sh"), task_type],
            capture_output=True, text=True, timeout=5
        )
        node = result.stdout.strip()
        return node if node and node != "none" else None
    except subprocess.TimeoutExpired:
        return None


def dispatch_command(task_type, command, cwd=None):
    """Route and execute a command on the best available node."""
    node = route_task(task_type)
    if not node:
        return {"error": f"no suitable node for task type '{task_type}'", "node": None}

    cmd = ["docker", "exec", GATEWAY_CONTAINER, "openclaw", "nodes", "run",
           "--node", node, "--raw", command]
    if cwd:
        cmd.extend(["--cwd", cwd])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        # Filter out plugin warnings
        output = "\n".join(
            line for line in result.stdout.splitlines()
            if "plugin" not in line.lower() and "Config warnings" not in line
            and not line.startswith("│") and not line.startswith("├")
            and not line.startswith("◇") and line.strip()
        )
        return {
            "node": node,
            "task_type": task_type,
            "command": command,
            "output": output,
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": "command timed out", "node": node}


class RouterHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress default logging

    def send_json(self, data, status=200):
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.send_json(get_cached_stats())
        elif self.path == "/nodes":
            self.send_json(get_cached_stats())
        elif self.path.startswith("/route/"):
            task_type = self.path.split("/route/")[1]
            node = route_task(task_type)
            if node:
                self.send_json({"task_type": task_type, "node": node})
            else:
                self.send_json({"task_type": task_type, "node": None,
                                "error": "no suitable node"}, 503)
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path == "/dispatch":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            task_type = body.get("task_type", "any")
            command = body.get("command")
            cwd = body.get("cwd")
            if not command:
                self.send_json({"error": "command required"}, 400)
                return
            result = dispatch_command(task_type, command, cwd)
            status = 200 if "error" not in result else 503
            self.send_json(result, status)
        else:
            self.send_json({"error": "not found"}, 404)


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), RouterHandler)
    print(f"OpenClaw Router API listening on port {PORT}")
    server.serve_forever()
