#!/usr/bin/env python3
"""Push cached node stats to Mission Control API."""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

CACHE_FILE = "/tmp/openclaw-node-stats.json"
MC_API = os.environ.get("MC_API_URL", "http://192.168.0.5:8000/api")
MC_KEY = os.environ.get("MC_API_KEY", "")

HARDWARE_OVERRIDES = {
    "heavy": "NiPoGi P2 16GB AMD Ryzen 3",
}


def _hardware_label(node):
    """Derive short hardware label from node stats."""
    mc_name = node.get("mc_name", node["name"])
    if mc_name in HARDWARE_OVERRIDES:
        return HARDWARE_OVERRIDES[mc_name]
    model = node.get("hardware_model", "")
    ram_gb = round(node.get("ram_total_mb", 0) / 1024)
    if "Raspberry Pi" in model:
        # "Raspberry Pi 5 Model B Rev 1.0" → "Pi 5"
        parts = model.split()
        pi_ver = f"Pi {parts[2]}" if len(parts) > 2 else "Pi"
        return f"{pi_ver} {ram_gb}GB"
    if model:
        return f"{model} {ram_gb}GB"
    return ""


def main():
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Cache error: {e}", file=sys.stderr)
        return

    nodes = data.get("nodes", data) if isinstance(data, dict) else data

    errors = 0
    for n in nodes:
        if not n.get("reachable"):
            continue

        mc_name = n.get("mc_name", n["name"])
        hw = _hardware_label(n)
        node_payload = {
            "name": mc_name,
            "hostname": n["host"],
            "framework": "OpenClaw",
            "status": "healthy" if n.get("connected") else "degraded",
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            "ram_total_mb": n.get("ram_total_mb", 0),
            "ram_used_mb": n.get("ram_used_mb", 0),
            "cpu_percent": n.get("load", 0),
            "metadata": {k: v for k, v in {
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
                # NVMe SMART metrics (only present on NVMe nodes)
                "nvme_wear_pct": n.get("nvme_wear_pct"),
                "nvme_spare_pct": n.get("nvme_spare_pct"),
                "nvme_temp_c": n.get("nvme_temp_c"),
                "nvme_written_gb": n.get("nvme_written_gb"),
                "nvme_media_errors": n.get("nvme_media_errors"),
                "nvme_power_on_hours": n.get("nvme_power_on_hours"),
                "nvme_unsafe_shutdowns": n.get("nvme_unsafe_shutdowns"),
                "disk_write_mb_s": n.get("disk_write_mb_s"),
            }.items() if v is not None},
        }
        if hw:
            node_payload["hardware"] = hw
        payload = json.dumps(node_payload).encode()

        pushed = False
        last_error = None
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
                pushed = True
                break
            except Exception as e:
                last_error = f"{method} {MC_API}{path}: {e}"
                continue

        if not pushed:
            print(f"WARN: failed to push {mc_name}: {last_error}", file=sys.stderr)
            errors += 1

    if errors:
        print(f"MC feed: {errors} node(s) failed to push", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
