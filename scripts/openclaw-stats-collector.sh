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
connected_raw=$(docker exec "$GATEWAY_CONTAINER" openclaw nodes status 2>&1 | grep "paired.*connected" | grep -v "disconnected" | grep -oP '^\│\s*\K\S+' | tr -d '│ ' 2>/dev/null || echo "")

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

# Feed Mission Control dashboard (if API is reachable)
MC_API="${MC_API:-http://192.168.0.22:8000/api}"
MC_KEY="${MC_KEY:-860e75126051c283758226e6852fcb687b1423c2b7c0af51}"

if curl -sf "$MC_API/health" > /dev/null 2>&1; then
    for node_json in $(python3 -c "
import json
with open('$CACHE_FILE') as f:
    data = json.load(f)
for n in data.get('nodes', []):
    if not n.get('reachable'):
        continue
    print(json.dumps({
        'name': n['name'],
        'hostname': n['host'],
        'framework': 'OpenClaw',
        'status': 'healthy' if n.get('connected') else 'degraded',
        'ram_total_mb': n.get('ram_avail_mb', 0) + int(n.get('ram_pct', 0) * n.get('ram_avail_mb', 0) / max(100 - n.get('ram_pct', 1), 1)),
        'ram_used_mb': int(n.get('ram_pct', 0) * n.get('ram_avail_mb', 0) / max(100 - n.get('ram_pct', 1), 1)),
        'cpu_percent': n.get('load', 0),
        'metadata': {'arch': n.get('arch', ''), 'cpus': n.get('cpus', 0), 'ram_pct': n.get('ram_pct', 0)}
    }))
" 2>/dev/null); do
        node_name=$(echo "$node_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['name'])")
        curl -sf -X PATCH "$MC_API/nodes/$node_name" \
            -H "Content-Type: application/json" \
            -H "X-Api-Key: $MC_KEY" \
            -d "$node_json" > /dev/null 2>&1 || \
        curl -sf -X POST "$MC_API/nodes" \
            -H "Content-Type: application/json" \
            -H "X-Api-Key: $MC_KEY" \
            -d "$node_json" > /dev/null 2>&1
    done
fi
