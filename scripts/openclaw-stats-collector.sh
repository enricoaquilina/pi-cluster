#!/bin/bash
# OpenClaw Node Stats Collector
# Runs on a timer, caches node health to /tmp/openclaw-node-stats.json
# Also feeds Mission Control dashboard via API.
#
# Usage: runs via systemd timer every 30 seconds
#   bash scripts/openclaw-stats-collector.sh

set -uo pipefail

CACHE_FILE="/tmp/openclaw-node-stats.json"
CONNECT_TIMEOUT=3
GATEWAY_CONTAINER="openclaw-openclaw-gateway-1"

# Node definitions: name:ssh_host:mc_name
NODES=("build:slave0:slave0" "light:slave1:slave1" "heavy:heavy:heavy")

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
        TEMP=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null | awk "{printf \"%.0f\", \$1/1000}" || echo "0")
        [ -z "$TEMP" ] && TEMP=0
        UPTIME_S=$(awk "{printf \"%.0f\", \$1}" /proc/uptime 2>/dev/null || echo "0")
        [ -z "$UPTIME_S" ] && UPTIME_S=0
        echo "$RAM_TOTAL $RAM_USED $RAM_AVAIL $RAM_PCT $LOAD $CPUS $ARCH $DISK_TOTAL $DISK_USED $DISK_AVAIL $DISK_PCT $TEMP $UPTIME_S"
    ' 2>/dev/null) || stats=""

    connected="false"
    is_connected "$name" && connected="true"

    if [ -z "$stats" ]; then
        json_nodes+=("{\"name\":\"$name\",\"host\":\"$ssh_host\",\"reachable\":false,\"connected\":$connected}")
        continue
    fi

    read -r ram_total ram_used ram_avail ram_pct load cpus arch disk_total disk_used disk_avail disk_pct temp uptime_s <<< "$stats"
    json_nodes+=("{\"name\":\"$name\",\"mc_name\":\"$mc_name\",\"host\":\"$ssh_host\",\"reachable\":true,\"connected\":$connected,\"ram_total_mb\":$ram_total,\"ram_used_mb\":$ram_used,\"ram_avail_mb\":$ram_avail,\"ram_pct\":$ram_pct,\"load\":$load,\"cpus\":$cpus,\"arch\":\"$arch\",\"disk_total_mb\":$disk_total,\"disk_used_mb\":$disk_used,\"disk_avail_mb\":$disk_avail,\"disk_pct\":$disk_pct,\"temp_c\":$temp,\"uptime_s\":$uptime_s}")
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
    python3 -c "
import json, urllib.request

with open('$CACHE_FILE') as f:
    data = json.load(f)

for n in data.get('nodes', []):
    if not n.get('reachable'):
        continue
    mc_name = n.get('mc_name', n['name'])
    payload = json.dumps({
        'name': mc_name,
        'hostname': n['host'],
        'framework': 'OpenClaw',
        'status': 'healthy' if n.get('connected') else 'degraded',
        'ram_total_mb': n.get('ram_total_mb', 0),
        'ram_used_mb': n.get('ram_used_mb', 0),
        'cpu_percent': n.get('load', 0),
        'metadata': {
            'arch': n.get('arch', ''),
            'cpus': n.get('cpus', 0),
            'ram_pct': n.get('ram_pct', 0),
            'disk_total_mb': n.get('disk_total_mb', 0),
            'disk_used_mb': n.get('disk_used_mb', 0),
            'disk_avail_mb': n.get('disk_avail_mb', 0),
            'disk_pct': n.get('disk_pct', 0),
            'temp_c': n.get('temp_c', 0),
            'uptime_s': n.get('uptime_s', 0),
            'connected': n.get('connected', False),
        },
    }).encode()

    for method in ['PATCH', 'POST']:
        path = f'/nodes/{mc_name}' if method == 'PATCH' else '/nodes'
        try:
            req = urllib.request.Request(
                '$MC_API' + path, data=payload, method=method,
                headers={'Content-Type': 'application/json', 'X-Api-Key': '$MC_KEY'},
            )
            urllib.request.urlopen(req, timeout=5)
            break
        except Exception:
            continue
" 2>/dev/null
fi
