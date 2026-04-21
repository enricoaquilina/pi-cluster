#!/bin/bash
# Checks: Polymarket copybot process + data freshness

check_polymarket_bot() {
    local is_active=false
    # Check for running copybot process (may run as raw process or systemd)
    if pgrep -f "copybot.bot" >/dev/null 2>&1; then
        is_active=true
    elif systemctl is-active --quiet polymarket-bot 2>/dev/null; then
        is_active=true
    fi
    if [[ "$is_active" != "true" ]]; then
        check_service "polymarket-bot" "down" "No copybot process running"
        return
    fi
    # Check data freshness - control.json should be updated within last hour
    local data_file="/mnt/external/polymarket-bot/data/control.json"
    if [ -f "$data_file" ]; then
        local age_s=$(( $(date +%s) - $(stat -c %Y "$data_file") ))
        if [ "$age_s" -gt 3600 ]; then
            check_service "polymarket-bot" "degraded" "Data stale (${age_s}s old)"
            return
        fi
    fi
    check_service "polymarket-bot" "up"
}
