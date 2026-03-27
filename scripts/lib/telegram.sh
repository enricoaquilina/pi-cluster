#!/bin/bash
# Shared Telegram notification helper for cluster scripts.
# Source this file in scripts: source "$SCRIPT_DIR/lib/telegram.sh"
#
# Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in environment
# (typically loaded from .env.cluster before sourcing this).
# Silently no-ops if either is unset.

send_telegram() {
    local msg="${1:-}"
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
        curl -sf -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${TELEGRAM_CHAT_ID}" \
            -d "text=${msg}" \
            -d "parse_mode=Markdown" > /dev/null 2>&1 || true
    fi
}
