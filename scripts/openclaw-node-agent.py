#!/usr/bin/env python3
"""
Lightweight node stats agent — runs on each node, pushes stats to master.
Replaces SSH-based polling with HTTP push (eliminates ~11K SSH sessions/day).

Usage: runs via systemd timer every 30 seconds on each node
  python3 scripts/openclaw-node-agent.py

Environment:
  OPENCLAW_NODE_NAME  — node name (build, light, heavy, control)
  OPENCLAW_MASTER_URL — master router API URL (default: http://192.168.0.5:8520)
"""

import json
import os
import socket
import sys
import urllib.request

NODE_NAME = os.environ.get("OPENCLAW_NODE_NAME", socket.gethostname())
MASTER_URL = os.environ.get("OPENCLAW_MASTER_URL", os.environ.get("CLUSTER_API_URL", "http://192.168.0.5:8520"))
NODE_PUSH_SECRET = os.environ.get("NODE_PUSH_SECRET", "")


def collect_stats():
    """Collect local system stats."""
    import re

    stats = {"name": NODE_NAME}

    # RAM
    with open("/proc/meminfo") as f:
        meminfo = f.read()
    mem_total = int(re.search(r"MemTotal:\s+(\d+)", meminfo).group(1)) // 1024
    mem_avail = int(re.search(r"MemAvailable:\s+(\d+)", meminfo).group(1)) // 1024
    mem_used = mem_total - mem_avail
    stats["ram_total_mb"] = mem_total
    stats["ram_used_mb"] = mem_used
    stats["ram_avail_mb"] = mem_avail
    stats["ram_pct"] = round(mem_used / mem_total * 100) if mem_total > 0 else 0

    # Swap
    swap_total = int(re.search(r"SwapTotal:\s+(\d+)", meminfo).group(1)) // 1024
    swap_free = int(re.search(r"SwapFree:\s+(\d+)", meminfo).group(1)) // 1024
    stats["swap_total_mb"] = swap_total
    stats["swap_used_mb"] = swap_total - swap_free

    # CPU
    stats["cpus"] = os.cpu_count() or 1
    with open("/proc/loadavg") as f:
        stats["load"] = float(f.read().split()[0])

    # Architecture
    stats["arch"] = os.uname().machine

    # Disk
    st = os.statvfs("/")
    disk_total = st.f_blocks * st.f_frsize // (1024 * 1024)
    disk_free = st.f_bavail * st.f_frsize // (1024 * 1024)
    disk_used = disk_total - disk_free
    stats["disk_total_mb"] = disk_total
    stats["disk_used_mb"] = disk_used
    stats["disk_avail_mb"] = disk_free
    stats["disk_pct"] = round(disk_used / disk_total * 100) if disk_total > 0 else 0

    # Temperature
    temp = 0
    for path in [
        "/sys/class/thermal/thermal_zone0/temp",
        "/sys/class/hwmon/hwmon1/temp1_input",
    ]:
        try:
            with open(path) as f:
                temp = int(f.read().strip()) // 1000
                break
        except (FileNotFoundError, ValueError):
            continue
    if temp == 0:
        # Fallback: find any hwmon temp
        import glob
        for p in glob.glob("/sys/class/hwmon/hwmon*/temp1_input"):
            try:
                with open(p) as f:
                    temp = int(f.read().strip()) // 1000
                    break
            except (FileNotFoundError, ValueError):
                continue
    stats["temp_c"] = temp

    # Uptime
    with open("/proc/uptime") as f:
        stats["uptime_s"] = int(float(f.read().split()[0]))

    return stats


def push_stats(stats):
    """Push stats to master's router API."""
    payload = json.dumps(stats).encode()
    headers = {"Content-Type": "application/json"}
    if NODE_PUSH_SECRET:
        headers["X-Node-Secret"] = NODE_PUSH_SECRET
    try:
        req = urllib.request.Request(
            f"{MASTER_URL}/node-stats",
            data=payload,
            method="POST",
            headers=headers,
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"Push failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    stats = collect_stats()
    push_stats(stats)
