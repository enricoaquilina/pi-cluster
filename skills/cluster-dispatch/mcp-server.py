#!/usr/bin/env python3
"""
OpenClaw MCP Server: Cluster Dispatch
Exposes cluster routing and model selection tools via the MCP stdio protocol.

Tools:
  - cluster_route: Select best node + model for a task type
  - cluster_dispatch: Execute a command on the best node
  - cluster_health: Get real-time cluster health
  - cluster_model: Get recommended model for a task type
"""

import json
import os
import subprocess
import sys
import time

CACHE_FILE = "/tmp/openclaw-node-stats.json"
SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts")
GATEWAY_CONTAINER = "openclaw-openclaw-gateway-1"

# Model routing: task_type -> preferred model + fallbacks
# Model allocation by task type
# Principle: best model for the job, with cost-aware fallbacks
#
# Coding:   Sonnet 4.6 (best code quality) → GLM-5 (complex systems) → MiniMax M2.7 (value)
# Research: Qwen 3.5 Plus (1M ctx, cheap) → MiniMax M2.7 (value) → Gemini Flash Lite (dirt cheap)
# Compute:  Opus 4.6 (best reasoning) → GPT-5.4 (large ctx) → GLM-5 (complex systems)
# Any:      MiniMax M2.7 (best value) → Gemini 2.5 Flash (fast) → DeepSeek V3.2 (cheap)
MODEL_CONFIG = {
    "research": {
        "primary": "openrouter/qwen/qwen3.5-plus-02-15",
        "fallbacks": [
            "openrouter/minimax/minimax-m2.7",
            "openrouter/google/gemini-2.5-flash-lite",
        ],
        "tier": "economy",
        "cost_limit_usd": 0.10,
    },
    "coding": {
        "primary": "openrouter/anthropic/claude-sonnet-4.6",
        "fallbacks": [
            "openrouter/z-ai/glm-5",
            "openrouter/minimax/minimax-m2.7",
        ],
        "tier": "standard",
        "cost_limit_usd": 1.00,
    },
    "compute": {
        "primary": "openrouter/anthropic/claude-opus-4.6",
        "fallbacks": [
            "openrouter/openai/gpt-5.4",
            "openrouter/z-ai/glm-5",
        ],
        "tier": "premium",
        "cost_limit_usd": 5.00,
    },
    "any": {
        "primary": "openrouter/minimax/minimax-m2.7",
        "fallbacks": [
            "openrouter/google/gemini-2.5-flash",
            "openrouter/deepseek/deepseek-v3.2",
        ],
        "tier": "standard",
        "cost_limit_usd": 0.25,
    },
}

TOOLS = [
    {
        "name": "cluster_route",
        "description": "Select the best cluster node and AI model for a given task type. Returns node name and recommended model.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_type": {
                    "type": "string",
                    "enum": ["coding", "research", "compute", "any"],
                    "description": "Type of task to route",
                }
            },
            "required": ["task_type"],
        },
    },
    {
        "name": "cluster_dispatch",
        "description": "Execute a shell command on the best available cluster node for the given task type.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_type": {
                    "type": "string",
                    "enum": ["coding", "research", "compute", "any"],
                    "description": "Type of task (determines which node to use)",
                },
                "command": {
                    "type": "string",
                    "description": "Shell command to execute on the target node",
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory on the remote node (default: /opt/workspace)",
                },
            },
            "required": ["task_type", "command"],
        },
    },
    {
        "name": "cluster_health",
        "description": "Get real-time health stats for all cluster nodes including RAM, CPU, architecture, and connectivity.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "cluster_model",
        "description": "Get the recommended AI model for a task type, with fallbacks and cost tier info.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_type": {
                    "type": "string",
                    "enum": ["coding", "research", "compute", "any"],
                    "description": "Type of task",
                }
            },
            "required": ["task_type"],
        },
    },
]


def route_task(task_type):
    """Select best node using the router script."""
    try:
        result = subprocess.run(
            [f"{SCRIPTS_DIR}/openclaw-router.sh", task_type],
            capture_output=True, text=True, timeout=5,
        )
        node = result.stdout.strip()
        return node if node and node != "none" else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def get_model(task_type):
    """Get recommended model config for task type."""
    return MODEL_CONFIG.get(task_type, MODEL_CONFIG["any"])


def dispatch_command(task_type, command, cwd=None):
    """Route and execute a command on the best node."""
    node = route_task(task_type)
    if not node:
        return {"error": f"No suitable node available for '{task_type}'"}

    cmd = ["docker", "exec", GATEWAY_CONTAINER, "openclaw", "nodes", "run",
           "--node", node, "--raw", command]
    if cwd:
        cmd.extend(["--cwd", cwd])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        output = "\n".join(
            line for line in result.stdout.splitlines()
            if "plugin" not in line.lower() and "Config warnings" not in line
            and not line.startswith("│") and not line.startswith("├")
            and not line.startswith("◇") and line.strip()
        )
        return {"node": node, "task_type": task_type, "output": output, "exit_code": result.returncode}
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out", "node": node}


def get_health():
    """Read cached node stats."""
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
        data["cache_age_seconds"] = round(time.time() - os.path.getmtime(CACHE_FILE), 1)
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {"error": "No cached stats available."}


def handle_request(request):
    """Handle a JSON-RPC request."""
    method = request.get("method")
    req_id = request.get("id")
    params = request.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "cluster-dispatch", "version": "2.0.0"},
            },
        }

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if tool_name == "cluster_route":
            task_type = arguments.get("task_type", "any")
            node = route_task(task_type)
            model = get_model(task_type)
            content = json.dumps({
                "node": node,
                "task_type": task_type,
                "model": model["primary"],
                "fallbacks": model["fallbacks"],
                "tier": model["tier"],
            })

        elif tool_name == "cluster_dispatch":
            result = dispatch_command(
                arguments.get("task_type", "any"),
                arguments.get("command", "echo 'no command'"),
                arguments.get("cwd"),
            )
            content = json.dumps(result)

        elif tool_name == "cluster_health":
            content = json.dumps(get_health(), indent=2)

        elif tool_name == "cluster_model":
            task_type = arguments.get("task_type", "any")
            model = get_model(task_type)
            content = json.dumps({
                "task_type": task_type,
                "primary": model["primary"],
                "fallbacks": model["fallbacks"],
                "tier": model["tier"],
                "cost_limit_usd": model["cost_limit_usd"],
            })

        else:
            return {
                "jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
            }

        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {"content": [{"type": "text", "text": content}]},
        }

    if method == "notifications/initialized":
        return None

    return {
        "jsonrpc": "2.0", "id": req_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    }


def main():
    """MCP stdio transport: read JSON-RPC from stdin, write to stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = handle_request(request)
            if response is not None:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
