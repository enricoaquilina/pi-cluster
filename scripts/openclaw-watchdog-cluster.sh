#!/bin/bash
# OpenClaw Cluster Watchdog
# Detects disconnected nodes, auto-re-pairs, and sends Telegram alerts.
# Managed by Ansible — do not edit manually.

set -uo pipefail

CACHE_FILE="/tmp/openclaw-node-stats.json"
STATE_FILE="/tmp/openclaw-watchdog-state.json"
SCRIPTS_DIR="/home/enrico/homelab/scripts"
GATEWAY_CONTAINER="openclaw-openclaw-gateway-1"
LOG_FILE="/var/log/openclaw-watchdog.log"

# shellcheck source=scripts/.env.cluster
[ -f "$SCRIPTS_DIR/.env.cluster" ] && source "$SCRIPTS_DIR/.env.cluster"

EXPECTED_NODES=("build" "light" "heavy")

log() {
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $1" | tee -a "$LOG_FILE"
}

send_telegram() {
    local message="$1"
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${TELEGRAM_CHAT_ID}" \
            -d "text=${message}" \
            -d "parse_mode=Markdown" > /dev/null 2>&1
    fi
}

load_state() {
    if [ -f "$STATE_FILE" ]; then
        cat "$STATE_FILE"
    else
        echo '{}'
    fi
}

save_state() {
    echo "$1" > "$STATE_FILE"
}

# Step 1: Check gateway health
gateway_status=$(docker ps --filter "name=$GATEWAY_CONTAINER" --format '{{.Status}}' 2>/dev/null || echo "")

if [ -z "$gateway_status" ]; then
    log "CRITICAL: Gateway container not found"
    prev_state=$(load_state)
    already_alerted=$(echo "$prev_state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('gateway_alert','false'))" 2>/dev/null || echo "false")
    if [ "$already_alerted" != "true" ]; then
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

# Step 2: Refresh stats cache
bash "$SCRIPTS_DIR/openclaw-stats-collector.sh" 2>/dev/null

if [ ! -f "$CACHE_FILE" ]; then
    log "ERROR: Stats cache not available"
    exit 1
fi

# Step 3: Check node connectivity
disconnected=()
connected=()

for node_name in "${EXPECTED_NODES[@]}"; do
    is_connected=$(python3 -c "
import json
with open('$CACHE_FILE') as f:
    data = json.load(f)
for n in data.get('nodes', []):
    if n['name'] == '$node_name':
        print('true' if n.get('connected') else 'false')
        exit()
print('false')
" 2>/dev/null)

    if [ "$is_connected" = "true" ]; then
        connected+=("$node_name")
    else
        disconnected+=("$node_name")
    fi
done

log "Connected: ${connected[*]:-none} | Disconnected: ${disconnected[*]:-none}"

# Step 4: Auto-recovery for disconnected nodes
if [ ${#disconnected[@]} -gt 0 ]; then    log "Attempting auto-recovery for: ${disconnected[*]}"

    bash "$SCRIPTS_DIR/openclaw-pair-nodes.sh" > /dev/null 2>&1
    repair_status=$?

    if [ $repair_status -eq 0 ]; then
        sleep 5
        bash "$SCRIPTS_DIR/openclaw-stats-collector.sh" 2>/dev/null

        still_disconnected=()
        for node_name in "${disconnected[@]}"; do
            is_now_connected=$(python3 -c "
import json
with open('$CACHE_FILE') as f:
    data = json.load(f)
for n in data.get('nodes', []):
    if n['name'] == '$node_name':
        print('true' if n.get('connected') else 'false')
        exit()
print('false')
" 2>/dev/null)
            if [ "$is_now_connected" != "true" ]; then
                still_disconnected+=("$node_name")
            fi
        done

        if [ ${#still_disconnected[@]} -eq 0 ]; then            log "Recovery successful: all nodes reconnected"
            save_state '{"gateway_alert":"false","node_alert":"false"}'
            send_telegram "🔄 *OpenClaw Auto-Recovery*
Detected disconnected: ${disconnected[*]}
Auto-repaired successfully — all nodes back online."
        else
            log "Recovery partial: still disconnected: ${still_disconnected[*]}"
            prev_state=$(load_state)
            already_alerted=$(echo "$prev_state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('node_alert','false'))" 2>/dev/null || echo "false")
            if [ "$already_alerted" != "true" ]; then
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
    prev_state=$(load_state)
    was_alerted=$(echo "$prev_state" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('node_alert','false') == 'true' or d.get('gateway_alert','false') == 'true')" 2>/dev/null || echo "false")
    if [ "$was_alerted" = "True" ]; then
        send_telegram "🟢 *OpenClaw Cluster Healthy*
All nodes connected: ${connected[*]}"
    fi
    save_state '{"gateway_alert":"false","node_alert":"false"}'
fi
