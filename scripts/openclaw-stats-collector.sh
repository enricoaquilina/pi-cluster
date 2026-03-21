#!/bin/bash
# OpenClaw Node Stats Collector
# Runs on a timer, caches node health to /tmp/openclaw-node-stats.json
# The router reads this cache instead of SSH-ing to each node live.
#
# Usage: runs via systemd timer every 30 seconds
#   bash scripts/openclaw-stats-collector.sh

set -uo pipefail

CACHE_FILE="/tmp/openclaw-node-stats.json"
CONNECT_TIMEOUT=3
GATEWAY_CONTAINER="openclaw-openclaw-gateway-1"

# Node definitions: name:ssh_host
NODES=("build:slave0" "light:slave1" "heavy:heavy")

# Get connected nodes from gateway (one call, cached for this run)
connected_raw=$(docker exec "$GATEWAY_CONTAINER" openclaw nodes status 2>&1 | grep "connected" | grep -oP '^\│\s*\K\S+' | tr -d '│ ' 2>/dev/null || echo "")

is_connected() {
    local name="$1"
    echo "$connected_raw" | grep -q "${name:0:4}"
}

# Collect stats in parallel
json_nodes=()
for node_def in "${NODES[@]}"; do
    IFS=: read -r name ssh_host <<< "$node_def"

    stats=$(ssh -o ConnectTimeout="$CONNECT_TIMEOUT" -o BatchMode=yes "$ssh_host" '
        RAM_TOTAL=$(free -m | awk "/^Mem:/ {print \$2}")
        RAM_USED=$(free -m | awk "/^Mem:/ {print \$3}")
        RAM_AVAIL=$(free -m | awk "/^Mem:/ {print \$7}")
        RAM_PCT=$(free | awk "/^Mem:/ {printf \"%.0f\", \$3/\$2 * 100}")
        LOAD=$(cat /proc/loadavg | cut -d" " -f1)
        CPUS=$(nproc)
        ARCH=$(uname -m)
        echo "$RAM_TOTAL $RAM_USED $RAM_AVAIL $RAM_PCT $LOAD $CPUS $ARCH"
    ' 2>/dev/null) || stats=""

    connected="false"
    is_connected "$name" && connected="true"

    if [ -z "$stats" ]; then
        json_nodes+=("{\"name\":\"$name\",\"host\":\"$ssh_host\",\"reachable\":false,\"connected\":$connected}")
        continue
    fi

    read -r _ram_total _ram_used ram_avail ram_pct load cpus arch <<< "$stats"
    json_nodes+=("{\"name\":\"$name\",\"host\":\"$ssh_host\",\"reachable\":true,\"connected\":$connected,\"ram_pct\":$ram_pct,\"ram_avail_mb\":$ram_avail,\"load\":$load,\"cpus\":$cpus,\"arch\":\"$arch\"}")
done

# Write cache atomically
nodes_json=$(IFS=,; echo "${json_nodes[*]}")
tmp_file=$(mktemp)
echo "{\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"nodes\":[$nodes_json]}" > "$tmp_file"
mv "$tmp_file" "$CACHE_FILE"
chmod 644 "$CACHE_FILE"
