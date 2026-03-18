#!/usr/bin/env python3
"""SSH proxy API for slave1 remote execution.

Runs on master as a lightweight HTTP API on port 8510.
OpenClaw agents call it via fetch/HTTP to execute commands on slave1.

Uses SSH ControlMaster for connection pooling (~5ms per command after first).
"""

import asyncio
import json
import shlex
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

SSH_TARGET = "enrico@192.168.0.4"
SSH_OPTS = [
    "-o", "ControlMaster=auto",
    "-o", "ControlPath=/home/enrico/.ssh/controlmasters/%r@%h:%p",
    "-o", "ControlPersist=600",
    "-o", "ConnectTimeout=5",
    "-o", "StrictHostKeyChecking=no",
    "-o", "BatchMode=yes",
]
TIMEOUT_SECONDS = 30

DANGEROUS_COMMANDS = ["rm -rf /", "mkfs", "dd if=", "shutdown", "reboot", "poweroff"]


async def ssh_exec(command: str, cwd: str | None = None) -> dict:
    """Execute a command on slave1 via SSH."""
    if cwd:
        remote_cmd = f"cd {shlex.quote(cwd)} && {command}"
    else:
        remote_cmd = command

    proc = await asyncio.create_subprocess_exec(
        "ssh", *SSH_OPTS, SSH_TARGET, remote_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        proc.kill()
        return {"output": f"Command timed out after {TIMEOUT_SECONDS}s", "exit_code": 124}

    return {
        "output": stdout.decode("utf-8", errors="replace"),
        "exit_code": proc.returncode or 0,
    }


async def exec_handler(request: Request) -> JSONResponse:
    """POST /exec — Execute a shell command on slave1."""
    body = await request.json()
    command = body.get("command", "")
    cwd = body.get("cwd")

    if not command:
        return JSONResponse({"error": "command is required"}, status_code=400)

    if any(d in command for d in DANGEROUS_COMMANDS):
        return JSONResponse({"error": "dangerous command blocked"}, status_code=403)

    result = await ssh_exec(command, cwd)
    return JSONResponse(result)


async def read_handler(request: Request) -> JSONResponse:
    """POST /read — Read a file on slave1."""
    body = await request.json()
    path = body.get("path", "")

    if not path:
        return JSONResponse({"error": "path is required"}, status_code=400)

    result = await ssh_exec(f"cat {shlex.quote(path)}")
    return JSONResponse(result)


async def status_handler(request: Request) -> JSONResponse:
    """GET /status — Get slave1 system status."""
    result = await ssh_exec(
        "echo '=== Memory ===' && free -h | grep Mem && "
        "echo '=== Disk ===' && df -h / /mnt/ssd 2>/dev/null && "
        "echo '=== Load ===' && uptime && "
        "echo '=== Services ===' && "
        "systemctl is-active pihole-FTL keepalived 2>/dev/null"
    )
    return JSONResponse(result)


async def health_handler(request: Request) -> JSONResponse:
    """GET /health — Quick health check."""
    result = await ssh_exec("echo ok")
    healthy = result["exit_code"] == 0
    return JSONResponse({"healthy": healthy}, status_code=200 if healthy else 503)


app = Starlette(
    routes=[
        Route("/exec", exec_handler, methods=["POST"]),
        Route("/read", read_handler, methods=["POST"]),
        Route("/status", status_handler, methods=["GET"]),
        Route("/health", health_handler, methods=["GET"]),
    ],
)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8510)
