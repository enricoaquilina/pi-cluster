#!/bin/bash
# Checks: Polymarket copybot process + data freshness

check_polymarket_bot() {
    if [ "$(hostname)" != "heavy" ]; then
        local status
        status=$(ssh -o ConnectTimeout=5 -o BatchMode=yes heavy \
            "systemctl is-active polymarket-bot 2>/dev/null" 2>/dev/null)
        local ssh_rc=$?
        if [ "$ssh_rc" -eq 255 ]; then
            check_service "polymarket-bot" "degraded" "Cannot reach heavy via SSH"
        elif [ "$status" = "active" ]; then
            check_service "polymarket-bot" "up"
        else
            check_service "polymarket-bot" "down" "Service not active on heavy"
        fi
        return
    fi

    local is_active=false
    if pgrep -f "copybot.bot" >/dev/null 2>&1; then
        is_active=true
    elif systemctl is-active --quiet polymarket-bot 2>/dev/null; then
        is_active=true
    fi
    if [[ "$is_active" != "true" ]]; then
        check_service "polymarket-bot" "down" "No copybot process running"
        return
    fi
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
