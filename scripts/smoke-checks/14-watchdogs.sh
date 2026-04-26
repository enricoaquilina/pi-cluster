#!/bin/bash
# Checks: Watchdog timer health — detects silently broken watchdogs
# Catches: watchdog scripts broken but timers still appear active

_WATCHDOG_STATUS=""
WATCHDOG_MAX_AGE="${WATCHDOG_MAX_AGE:-300}"

_fetch_watchdog_status() {
    [ -n "$_WATCHDOG_STATUS" ] && return
    local result="" now_ts
    now_ts=$(date +%s)

    for timer in openclaw-watchdog-cluster ssh-watchdog; do
        local active last_wall age
        active=$(systemctl is-active "${timer}.timer" 2>/dev/null || echo "inactive")
        if [ "$active" != "active" ]; then
            result+="${timer} inactive 0"$'\n'
            continue
        fi
        last_wall=$(systemctl show "${timer}.service" \
            --property=InactiveEnterTimestamp --value 2>/dev/null || echo "")
        if [ -n "$last_wall" ] && [ "$last_wall" != "" ]; then
            local last_epoch
            last_epoch=$(date -d "$last_wall" +%s 2>/dev/null || echo "0")
            age=$(( now_ts - last_epoch ))
        else
            age=-1
        fi
        result+="${timer} ${active} ${age}"$'\n'
    done

    _WATCHDOG_STATUS="$result"
}

_check_watchdog() {
    local svc_name="$1" timer_name="$2"
    _fetch_watchdog_status

    local line active age
    line=$(echo "$_WATCHDOG_STATUS" | grep "^${timer_name} ")
    if [ -z "$line" ]; then
        check_service "$svc_name" "down" "Timer status unknown"
        return
    fi

    active=$(echo "$line" | awk '{print $2}')
    age=$(echo "$line" | awk '{print $3}')

    if [ "$active" = "inactive" ]; then
        check_service "$svc_name" "down" "Timer not active"
        return
    fi

    if [ "$age" = "-1" ]; then
        check_service "$svc_name" "degraded" "Timer active but service never ran"
        return
    fi

    if [ "$age" -gt "$WATCHDOG_MAX_AGE" ]; then
        local age_min=$(( age / 60 ))
        check_service "$svc_name" "down" "Last run ${age_min}m ago (threshold ${WATCHDOG_MAX_AGE}s)"
    else
        check_service "$svc_name" "up"
    fi
}

check_watchdog_cluster() {
    _check_watchdog "watchdog-cluster" "openclaw-watchdog-cluster"

    if [ "${RESULTS[watchdog-cluster]:-}" = "up" ]; then
        local log="/tmp/openclaw-watchdog.log"
        if [ -f "$log" ]; then
            local log_age=$(( $(date +%s) - $(stat -c %Y "$log") ))
            if [ "$log_age" -gt "$WATCHDOG_MAX_AGE" ]; then
                check_service "watchdog-cluster" "degraded" "Timer active but log stale (${log_age}s)"
            fi
        fi
    fi
}

check_watchdog_ssh() {
    _check_watchdog "watchdog-ssh" "ssh-watchdog"
}
