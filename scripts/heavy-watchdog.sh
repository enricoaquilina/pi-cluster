#!/bin/bash
set -uo pipefail
# Monitors heavy node health. After 3 consecutive failures (6 min), auto-restores
# services to master. Runs every 2 min via cron on master.
#
# Checks: ping + gateway health endpoint on heavy.
# State: /tmp/heavy-watchdog-state (failure counter)
#
# Usage: cron every 2 min on master

STATE_FILE="/tmp/heavy-watchdog-state"
FAIL_THRESHOLD=3
GATEWAY_PORT="18789"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/.env.cluster" 2>/dev/null || true
HEAVY_IP="${HEAVY_IP:-192.168.0.5}"

send_telegram() {
    local msg="$1"
    curl -sf -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d "chat_id=${TELEGRAM_CHAT_ID}&text=$msg" >/dev/null 2>&1 || true
}

# Check if heavy is reachable (ping + gateway health)
if ping -c1 -W3 "$HEAVY_IP" >/dev/null 2>&1 && \
   curl -sf --connect-timeout 5 "http://$HEAVY_IP:$GATEWAY_PORT/healthz" >/dev/null 2>&1; then
    # Heavy is up — reset counter
    echo "0" > "$STATE_FILE"
    exit 0
fi

# Heavy is down — increment counter
FAIL_COUNT=$(cat "$STATE_FILE" 2>/dev/null || echo "0")
FAIL_COUNT=$((FAIL_COUNT + 1))
echo "$FAIL_COUNT" > "$STATE_FILE"

if [ "$FAIL_COUNT" -eq 1 ]; then
    send_telegram "⚠️ Heavy ($HEAVY_IP) unreachable (attempt $FAIL_COUNT/$FAIL_THRESHOLD). Monitoring..."

elif [ "$FAIL_COUNT" -ge "$FAIL_THRESHOLD" ]; then
    # Check if master services already running (avoid duplicate restore)
    if docker ps --filter "name=openclaw-openclaw-gateway" --format '{{.Names}}' 2>/dev/null | grep -q openclaw; then
        exit 0
    fi

    send_telegram "🚨 Heavy down for $((FAIL_COUNT * 2)) min. AUTO-RESTORING services to master..."
    bash "$SCRIPT_DIR/emergency-restore-master.sh"
fi
