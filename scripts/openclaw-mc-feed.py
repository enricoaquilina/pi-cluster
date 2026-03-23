#!/usr/bin/env python3
"""Push cached node stats to Mission Control API."""

import json
import os
import sys
import urllib.request

CACHE_FILE = "/tmp/openclaw-node-stats.json"
MC_API = "http://127.0.0.1:8000/api"
MC_KEY = os.environ.get("MC_API_KEY", "")


def main():
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Cache error: {e}", file=sys.stderr)
        return

    for n in data.get("nodes", []):
        if not n.get("reachable"):
            continue

        mc_name = n.get("mc_name", n["name"])
        payload = json.dumps({
            "name": mc_name,
            "hostname": n["host"],
            "framework": "OpenClaw",
            "status": "healthy" if n.get("connected") else "degraded",
            "ram_total_mb": n.get("ram_total_mb", 0),
            "ram_used_mb": n.get("ram_used_mb", 0),
            "cpu_percent": n.get("load", 0),
            "metadata": {
                "arch": n.get("arch", ""),
                "cpus": n.get("cpus", 0),
                "ram_pct": n.get("ram_pct", 0),
                "disk_total_mb": n.get("disk_total_mb", 0),
                "disk_used_mb": n.get("disk_used_mb", 0),
                "disk_avail_mb": n.get("disk_avail_mb", 0),
                "disk_pct": n.get("disk_pct", 0),
                "temp_c": n.get("temp_c", 0),
                "uptime_s": n.get("uptime_s", 0),
                "swap_total_mb": n.get("swap_total_mb", 0),
                "swap_used_mb": n.get("swap_used_mb", 0),
                "connected": n.get("connected", False),
            },
        }).encode()

        for method in ["PATCH", "POST"]:
            path = f"/nodes/{mc_name}" if method == "PATCH" else "/nodes"
            try:
                req = urllib.request.Request(
                    MC_API + path,
                    data=payload,
                    method=method,
                    headers={
                        "Content-Type": "application/json",
                        "X-Api-Key": MC_KEY,
                    },
                )
                urllib.request.urlopen(req, timeout=5)
                break
            except Exception:
                continue


if __name__ == "__main__":
    main()
