#!/bin/bash
# Checks: OpenClaw Telegram polling + WhatsApp connectivity

check_openclaw_telegram() {
    if [[ "$HOST_DNS_OK" == false ]]; then
        check_service "openclaw-telegram" "down" "Host DNS down"
        return
    fi
    # Check if container can resolve Telegram API
    if ! timed_ssh 8 ${HEAVY_HOST} "docker exec openclaw-openclaw-gateway-1 getent hosts api.telegram.org" >/dev/null 2>&1; then
        check_service "openclaw-telegram" "down" "DNS resolution for api.telegram.org failed inside container"
        return
    fi
    # Check recent logs for polling activity (successful getUpdates within last 5 min)
    local recent_logs
    recent_logs=$(timed_ssh 10 ${HEAVY_HOST} "docker logs --since 5m openclaw-openclaw-gateway-1" 2>&1 | tail -50)
    if echo "$recent_logs" | grep -v "409: Conflict" | grep -qi "getUpdates.*failed\|error.*telegram\|ENOTFOUND\|ETIMEDOUT\|telegram.*ECONNREFUSED" 2>/dev/null; then
        check_service "openclaw-telegram" "down" "Telegram polling errors in recent logs"
        return
    fi
    check_service "openclaw-telegram" "up"
}

check_openclaw_whatsapp() {
    local recent_logs
    recent_logs=$(timed_ssh 10 ${HEAVY_HOST} "docker logs --since 10m openclaw-openclaw-gateway-1" 2>&1 | tail -100)
    # Positive signal: provider is actively listening
    if echo "$recent_logs" | grep -qi "Listening for personal WhatsApp" 2>/dev/null; then
        check_service "openclaw-whatsapp" "up"
        return
    fi
    # Negative signal: auth/connection errors (not stale-socket — that's normal health-monitor)
    if echo "$recent_logs" | grep -qi "whatsapp.*auth.*fail\|whatsapp.*error.*401\|whatsapp.*logged.out" 2>/dev/null; then
        check_service "openclaw-whatsapp" "down" "WhatsApp auth/connection failure"
        return
    fi
    # No signal either way — provider may be between restarts
    check_service "openclaw-whatsapp" "up"
}
