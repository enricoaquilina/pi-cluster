#!/bin/bash
# OpenClaw Node Stats Collector
# Runs on a timer, caches node health to /tmp/openclaw-node-stats.json
# Also feeds Mission Control dashboard via openclaw-mc-feed.py.
#
# Usage: runs via systemd timer every 30 seconds
#   bash scripts/openclaw-stats-collector.sh

set -uo pipefail

CACHE_FILE="/tmp/openclaw-node-stats.json"
CONNECT_TIMEOUT=3
GATEWAY_CONTAINER="openclaw-openclaw-gateway-1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Node definitions: name:ssh_host:mc_name
NODES=("master:master:master" "build:slave0:slave0" "light:slave1:slave1" "heavy:heavy:heavy")

# Get connected nodes from gateway (one call, cached for this run)
connected_raw=$(docker exec "$GATEWAY_CONTAINER" openclaw nodes status 2>&1 | grep "paired.*connected" | grep -v "disconnected" | grep -oP '^\│\s*\K\S+' | tr -d '│ ' 2>/dev/null || echo "")

is_connected() {
    local name="$1"
    echo "$connected_raw" | grep -q "${name:0:4}"
}

# Collect stats
json_nodes=()
for node_def in "${NODES[@]}"; do
    IFS=: read -r name ssh_host mc_name <<< "$node_def"

    stats=$(ssh -o ConnectTimeout="$CONNECT_TIMEOUT" -o BatchMode=yes "$ssh_host" '
        RAM_TOTAL=$(free -m | awk "/^Mem:/ {print \$2}")
        RAM_USED=$(free -m | awk "/^Mem:/ {print \$3}")
        RAM_AVAIL=$(free -m | awk "/^Mem:/ {print \$7}")
        RAM_PCT=$(free | awk "/^Mem:/ {printf \"%.0f\", \$3/\$2 * 100}")
        LOAD=$(cat /proc/loadavg | cut -d" " -f1)
        CPUS=$(nproc)
        ARCH=$(uname -m)
        DISK_TOTAL=$(df -BM / | tail -1 | awk "{print \$2}" | tr -d "M")
        DISK_USED=$(df -BM / | tail -1 | awk "{print \$3}" | tr -d "M")
        DISK_AVAIL=$(df -BM / | tail -1 | awk "{print \$4}" | tr -d "M")
        DISK_PCT=$(df / | tail -1 | awk "{print \$5}" | tr -d "%")
        SWAP_TOTAL=$(free -m | awk "/^Swap:/ {print \$2}")
        SWAP_USED=$(free -m | awk "/^Swap:/ {print \$3}")
        TEMP=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null | awk "{printf \"%.0f\", \$1/1000}" || echo "")
        [ -z "$TEMP" ] && TEMP=$(cat /sys/class/hwmon/hwmon1/temp1_input 2>/dev/null | awk "{printf \"%.0f\", \$1/1000}" || echo "")
        [ -z "$TEMP" ] && TEMP=$(find /sys/class/hwmon -name "temp1_input" -exec cat {} \; 2>/dev/null | head -1 | awk "{printf \"%.0f\", \$1/1000}" || echo "0")
        [ -z "$TEMP" ] && TEMP=0
        UPTIME_S=$(awk "{printf \"%.0f\", \$1}" /proc/uptime 2>/dev/null || echo "0")
        [ -z "$UPTIME_S" ] && UPTIME_S=0
        echo "$RAM_TOTAL $RAM_USED $RAM_AVAIL $RAM_PCT $LOAD $CPUS $ARCH $DISK_TOTAL $DISK_USED $DISK_AVAIL $DISK_PCT $TEMP $UPTIME_S $SWAP_TOTAL $SWAP_USED"
    ' 2>/dev/null) || stats=""

    connected="false"
    is_connected "$name" && connected="true"

    if [ -z "$stats" ]; then
        json_nodes+=("{\"name\":\"$name\",\"mc_name\":\"$mc_name\",\"host\":\"$ssh_host\",\"reachable\":false,\"connected\":$connected}")
        continue
    fi

    read -r ram_total ram_used ram_avail ram_pct load cpus arch disk_total disk_used disk_avail disk_pct temp uptime_s swap_total swap_used <<< "$stats"
    json_nodes+=("{\"name\":\"$name\",\"mc_name\":\"$mc_name\",\"host\":\"$ssh_host\",\"reachable\":true,\"connected\":$connected,\"ram_total_mb\":$ram_total,\"ram_used_mb\":$ram_used,\"ram_avail_mb\":$ram_avail,\"ram_pct\":$ram_pct,\"load\":$load,\"cpus\":$cpus,\"arch\":\"$arch\",\"disk_total_mb\":$disk_total,\"disk_used_mb\":$disk_used,\"disk_avail_mb\":$disk_avail,\"disk_pct\":$disk_pct,\"temp_c\":$temp,\"uptime_s\":$uptime_s,\"swap_total_mb\":${swap_total:-0},\"swap_used_mb\":${swap_used:-0}}")
done

# Write cache atomically
nodes_json=$(IFS=,; echo "${json_nodes[*]}")
tmp_file=$(mktemp)
echo "{\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"nodes\":[$nodes_json]}" > "$tmp_file"
mv "$tmp_file" "$CACHE_FILE"
chmod 644 "$CACHE_FILE"

# Feed Mission Control dashboard
python3 "$SCRIPT_DIR/openclaw-mc-feed.py" 2>/dev/null || true
