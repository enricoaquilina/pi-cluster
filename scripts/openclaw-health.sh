#!/bin/bash
# OpenClaw Cluster Health Check
# Runs from master, checks all nodes and services
#
# Usage:
#   bash scripts/openclaw-health.sh          # Human-readable output
#   bash scripts/openclaw-health.sh --json    # JSON output for programmatic use

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$SCRIPT_DIR/.env.cluster" ] && source "$SCRIPT_DIR/.env.cluster"

THRESHOLD_RAM=85
JSON_MODE=false
[ "${1:-}" = "--json" ] && JSON_MODE=true

# Node definitions: display_name:ssh_host
NODES=("build:slave0" "light:slave1" "heavy:heavy")
GATEWAY_CONTAINER="openclaw-openclaw-gateway-1"

json_nodes=()

for node_def in "${NODES[@]}"; do
    IFS=: read -r name ssh_host <<< "$node_def"

    # Check SSH connectivity and get stats
    stats=$(ssh -o ConnectTimeout=3 -o BatchMode=yes "$ssh_host" '
        RAM_TOTAL=$(free -m | awk "/^Mem:/ {print \$2}")
        RAM_USED=$(free -m | awk "/^Mem:/ {print \$3}")
        RAM_AVAIL=$(free -m | awk "/^Mem:/ {print \$7}")
        RAM_PCT=$(free | awk "/^Mem:/ {printf \"%.0f\", \$3/\$2 * 100}")
        LOAD=$(cat /proc/loadavg | cut -d" " -f1)
        CPUS=$(nproc)
        ARCH=$(uname -m)
        NFS_WORKSPACE=$(mountpoint -q /opt/workspace 2>/dev/null && echo "mounted" || echo "unmounted")
        NFS_EXTERNAL=$(mountpoint -q /mnt/external 2>/dev/null && echo "mounted" || echo "unmounted")
        echo "$RAM_TOTAL $RAM_USED $RAM_AVAIL $RAM_PCT $LOAD $CPUS $ARCH $NFS_WORKSPACE $NFS_EXTERNAL"
    ' 2>/dev/null) || stats=""

    # Check gateway connection
    connected="false"
    if docker exec "$GATEWAY_CONTAINER" sh -c "OPENCLAW_GATEWAY_TOKEN=$OPENCLAW_GATEWAY_TOKEN timeout 10 node dist/index.js nodes status 2>&1" | grep -q "$name.*connected"; then
        connected="true"
    fi

    if [ -z "$stats" ]; then
        if $JSON_MODE; then
            json_nodes+=("{\"name\":\"$name\",\"host\":\"$ssh_host\",\"ssh\":\"unreachable\",\"connected\":$connected}")
        else
            echo "$name ($ssh_host): SSH UNREACHABLE | gateway: $([ "$connected" = "true" ] && echo "connected" || echo "disconnected")"
        fi
        continue
    fi

    read -r ram_total ram_used ram_avail ram_pct load cpus arch nfs_ws nfs_ext <<< "$stats"

    ram_status="OK"
    [ "${ram_pct:-0}" -gt "$THRESHOLD_RAM" ] && ram_status="HIGH"

    if $JSON_MODE; then
        json_nodes+=("{\"name\":\"$name\",\"host\":\"$ssh_host\",\"ssh\":\"ok\",\"connected\":$connected,\"ram\":{\"total\":$ram_total,\"used\":$ram_used,\"available\":$ram_avail,\"percent\":$ram_pct},\"load\":$load,\"cpus\":$cpus,\"arch\":\"$arch\",\"nfs\":{\"workspace\":\"$nfs_ws\",\"external\":\"$nfs_ext\"}}")
    else
        echo "$name ($ssh_host): RAM ${ram_pct}% ($ram_status) | ${ram_avail}MB free | load $load | ${cpus}x $arch | NFS ws:$nfs_ws ext:$nfs_ext | gateway: $([ "$connected" = "true" ] && echo "connected" || echo "disconnected")"
    fi
done

# Gateway status
gw_status="unknown"
if docker ps --filter "name=$GATEWAY_CONTAINER" --format '{{.Status}}' 2>/dev/null | grep -q "healthy"; then
    gw_status="healthy"
elif docker ps --filter "name=$GATEWAY_CONTAINER" --format '{{.Status}}' 2>/dev/null | grep -q "Up"; then
    gw_status="running"
else
    gw_status="down"
fi

if $JSON_MODE; then
    # Build JSON output
    nodes_json=$(IFS=,; echo "${json_nodes[*]}")
    echo "{\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"gateway\":\"$gw_status\",\"nodes\":[$nodes_json]}"
else
    echo ""
    echo "Gateway: $gw_status"
    echo ""
    echo "=== Health Check Complete $(date +%H:%M:%S) ==="
fi
