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
import subprocess
import sys
import time
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

    # NVMe SMART health (only on nodes with NVMe)
    nvme = _collect_nvme_smart()
    if nvme:
        stats.update(nvme)

    # Disk write rate (MB/s over 3s sample)
    write_rate = _collect_write_rate()
    if write_rate is not None:
        stats["disk_write_mb_s"] = write_rate

    return stats


def _collect_nvme_smart():
    """Collect NVMe SMART metrics via smartctl. Returns dict or None."""
    import re
    # Find NVMe device
    nvme_dev = None
    try:
        out = subprocess.run(["lsblk", "-dpno", "NAME,TYPE"],
                             capture_output=True, text=True, timeout=5)
        for line in out.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == "disk" and "nvme" in parts[0]:
                nvme_dev = parts[0]
                break
    except Exception:
        pass
    if not nvme_dev:
        return None

    try:
        result = subprocess.run(
            ["sudo", "smartctl", "-A", nvme_dev],
            capture_output=True, text=True, timeout=10)
        text = result.stdout
    except Exception:
        return None

    smart = {}

    m = re.search(r"Percentage Used:\s+(\d+)%", text)
    if m:
        smart["nvme_wear_pct"] = int(m.group(1))

    m = re.search(r"Available Spare:\s+(\d+)%", text)
    if m:
        smart["nvme_spare_pct"] = int(m.group(1))

    m = re.search(r"Temperature:\s+(\d+)\s+Celsius", text)
    if m:
        smart["nvme_temp_c"] = int(m.group(1))

    m = re.search(r"Data Units Written:\s+([\d,]+)", text)
    if m:
        # Each data unit = 512KB = 0.5MB. Convert to GB.
        units = int(m.group(1).replace(",", ""))
        smart["nvme_written_gb"] = round(units * 512 / 1024 / 1024)

    m = re.search(r"Media and Data Integrity Errors:\s+(\d+)", text)
    if m:
        smart["nvme_media_errors"] = int(m.group(1))

    m = re.search(r"Power On Hours:\s+(\d+)", text)
    if m:
        smart["nvme_power_on_hours"] = int(m.group(1))

    m = re.search(r"Unsafe Shutdowns:\s+(\d+)", text)
    if m:
        smart["nvme_unsafe_shutdowns"] = int(m.group(1))

    return smart if smart else None


def _collect_write_rate():
    """Measure disk write rate over a brief sample. Returns MB/s or None."""
    # Find the NVMe disk name for /proc/diskstats
    disk_name = None
    try:
        with open("/proc/diskstats") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 14 and "nvme" in parts[2] and "p" not in parts[2]:
                    disk_name = parts[2]
                    break
    except Exception:
        return None
    if not disk_name:
        return None

    def read_write_sectors():
        with open("/proc/diskstats") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 14 and parts[2] == disk_name:
                    return int(parts[9])  # write sectors field
        return 0

    s1 = read_write_sectors()
    time.sleep(3)
    s2 = read_write_sectors()
    # 512 bytes per sector
    return round((s2 - s1) * 512 / 3 / 1048576, 1)


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
