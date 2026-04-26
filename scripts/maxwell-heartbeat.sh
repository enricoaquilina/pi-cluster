#!/usr/bin/env bash
# Maxwell heartbeat — runs hourly via cron on master
# Executes heartbeat-runner.py: health checks + PRD-gated dispatch
set -euo pipefail

LOCK_FILE="/var/run/maxwell-heartbeat.lock"
LOG_FILE="/var/log/maxwell-heartbeat.log"
STATE_DIR="/var/run/cluster-health"
RUNNER="/home/enrico/homelab/scripts/heartbeat-runner.py"

# Prevent overlapping runs
exec 200>"$LOCK_FILE"
flock --nonblock 200 || { echo "$(date -Iseconds) SKIP: previous run still active" >> "$LOG_FILE"; exit 0; }

# Source Telegram credentials (same as cluster-alert.sh)
[ -f /etc/cluster-alert.env ] && source /etc/cluster-alert.env
export TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
export TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"

# Host-side paths
export POLYBOT_DATA_DIR="/mnt/external/polymarket-bot/data"
export MISSION_CONTROL_URL="http://192.168.0.5:8000/api"
MISSION_CONTROL_API_KEY="$(grep '^API_KEY=' /mnt/external/mission-control/.env | cut -d= -f2)"
export MISSION_CONTROL_API_KEY
export OPENCLAW_WORKSPACE="/mnt/external/openclaw/workspace"
export LIFE_DIR="/home/enrico/life"
OPENROUTER_API_KEY="$(grep '^OPENROUTER_API_KEY=' /mnt/external/mission-control/.env | cut -d= -f2)"
export OPENROUTER_API_KEY

# Run heartbeat
echo "$(date -Iseconds) START" >> "$LOG_FILE"
if python3 "$RUNNER" heartbeat --telegram >> "$LOG_FILE" 2>&1; then
    echo "$(date -Iseconds) OK" >> "$LOG_FILE"
    rm -f "$STATE_DIR/heartbeat-runner.fail"
else
    echo "$(date -Iseconds) FAIL (exit $?)" >> "$LOG_FILE"
    if [ ! -f "$STATE_DIR/heartbeat-runner.fail" ]; then
        /usr/local/bin/cluster-alert.sh "Maxwell heartbeat-runner FAILED (check /var/log/maxwell-heartbeat.log)" 2>/dev/null || true
        touch "$STATE_DIR/heartbeat-runner.fail"
    fi
fi
