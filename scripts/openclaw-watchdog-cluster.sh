#!/bin/bash
# OpenClaw Cluster Watchdog
# Detects disconnected nodes, auto-re-pairs, and sends Telegram alerts.
# Managed by Ansible — do not edit manually.

set -uo pipefail

if [ "$(hostname)" != "heavy" ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Cluster watchdog only runs on heavy. Exiting." >&2
    exit 0
fi

CACHE_FILE="${CACHE_FILE:-/tmp/openclaw-node-stats.json}"
STATE_FILE="${STATE_FILE:-/tmp/openclaw-watchdog-state.json}"
SCRIPT_DIR="${SCRIPT_DIR:-/home/enrico/pi-cluster/scripts}"
GATEWAY_CONTAINER="${GATEWAY_CONTAINER:-openclaw-openclaw-gateway-1}"
LOG_FILE="${LOG_FILE:-/tmp/openclaw-watchdog.log}"

# shellcheck source=scripts/.env.cluster
[ -f "$SCRIPT_DIR/.env.cluster" ] && source "$SCRIPT_DIR/.env.cluster"
# shellcheck source=scripts/lib/telegram.sh
source "$SCRIPT_DIR/lib/telegram.sh" 2>/dev/null || send_telegram() { :; }

EXPECTED_NODES=("build" "light" "heavy")

log() {
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $1" | tee -a "$LOG_FILE"
}

get_state() {
    local val
    val=$(grep -o "\"$1\":\"[^\"]*\"" "$STATE_FILE" 2>/dev/null | cut -d'"' -f4)
    echo "${val:-false}"
}

save_state() {
    echo "$1" > "$STATE_FILE"
}

# Step 1: Check gateway health (only on the node that runs it)
if [ "$(hostname)" = "heavy" ]; then
    gateway_status=$(docker ps --filter "name=$GATEWAY_CONTAINER" --format '{{.Status}}' 2>/dev/null || echo "")

    if [ -z "$gateway_status" ]; then
        log "CRITICAL: Gateway container not found"
        if [ "$(get_state gateway_alert)" != "true" ]; then
            send_telegram "🔴 *OpenClaw Gateway DOWN*
Container \`$GATEWAY_CONTAINER\` not found.
Manual intervention required."
            save_state '{"gateway_alert":"true"}'
        fi
        exit 1
    fi

    if echo "$gateway_status" | grep -qi "restarting"; then
        log "WARNING: Gateway is restarting, waiting..."
        exit 0
    fi
fi

# Step 2: Refresh stats cache
if [ "$(hostname)" = "heavy" ]; then
    bash "$SCRIPT_DIR/openclaw-stats-collector.sh" 2>/dev/null
else
    # On non-heavy nodes, pull node status from MC API
    curl -sf --max-time 10 http://192.168.0.5:8000/api/nodes > "$CACHE_FILE" 2>/dev/null
fi

if [ ! -f "$CACHE_FILE" ]; then
    log "ERROR: Stats cache not available"
    exit 1
fi

# Step 3: Check node connectivity (single Python call for all nodes)
disconnected=()
connected=()

node_status=$(python3 -c "
import json
with open('$CACHE_FILE') as f:
    data = json.load(f)
if isinstance(data, list):
    nodes = data
else:
    nodes = data.get('nodes', [])
status = {n['name']: n.get('connected', False) or n.get('metadata', {}).get('connected', False) for n in nodes}
for name in '${EXPECTED_NODES[*]}'.split():
    print(name, 'true' if status.get(name) else 'false')
" 2>/dev/null)

while read -r node_name is_connected; do
    if [ "$is_connected" = "true" ]; then
        connected+=("$node_name")
    else
        disconnected+=("$node_name")
    fi
done <<< "$node_status"

log "Connected: ${connected[*]:-none} | Disconnected: ${disconnected[*]:-none}"

# Step 4: Auto-recovery for disconnected nodes
if [ ${#disconnected[@]} -gt 0 ]; then    log "Attempting auto-recovery for: ${disconnected[*]}"

    bash "$SCRIPT_DIR/openclaw-pair-nodes.sh" > /dev/null 2>&1
    repair_status=$?

    if [ $repair_status -eq 0 ]; then
        sleep 5
        bash "$SCRIPT_DIR/openclaw-stats-collector.sh" 2>/dev/null

        still_disconnected=()
        recheck=$(python3 -c "
import json
with open('$CACHE_FILE') as f:
    data = json.load(f)
status = {n['name']: n.get('connected', False) for n in data.get('nodes', [])}
for name in '${disconnected[*]}'.split():
    print(name, 'true' if status.get(name) else 'false')
" 2>/dev/null)
        while read -r node_name is_now; do
            [ "$is_now" != "true" ] && still_disconnected+=("$node_name")
        done <<< "$recheck"

        if [ ${#still_disconnected[@]} -eq 0 ]; then            log "Recovery successful: all nodes reconnected"
            save_state '{"gateway_alert":"false","node_alert":"false"}'
            send_telegram "🔄 *OpenClaw Auto-Recovery*
Detected disconnected: ${disconnected[*]}
Auto-repaired successfully — all nodes back online."
        else
            log "Recovery partial: still disconnected: ${still_disconnected[*]}"
            if [ "$(get_state node_alert)" != "true" ]; then
                send_telegram "🟡 *OpenClaw Nodes Disconnected*
Failed to reconnect: ${still_disconnected[*]}
Connected: ${connected[*]}
Auto-repair attempted but failed."
                save_state '{"gateway_alert":"false","node_alert":"true"}'
            fi
        fi
    else
        log "Recovery failed: pair script returned $repair_status"
    fi
else
    if [ "$(get_state node_alert)" = "true" ] || [ "$(get_state gateway_alert)" = "true" ]; then
        send_telegram "🟢 *OpenClaw Cluster Healthy*
All nodes connected: ${connected[*]}"
    fi
    save_state '{"gateway_alert":"false","node_alert":"false"}'
fi
