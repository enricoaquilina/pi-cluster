#!/bin/bash
set -uo pipefail
# Cluster alert — sends message to Telegram + syslog.
# Deployed to /usr/local/bin/cluster-alert.sh by openclaw-monitoring.yml.
# Referenced by system-smoke-test.sh via ALERT_SCRIPT.
#
# Usage: cluster-alert.sh "message text"

source /usr/local/bin/.env.cluster 2>/dev/null || true

msg="${1:-alert}"

if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
    curl -sf -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
        --data-urlencode "text=${msg}" \
        -d "parse_mode=Markdown" > /dev/null 2>&1 || true
fi

logger -t "cluster-alert" "$msg"
