#!/bin/bash
# Checks: Gravity Sync timer health + last run status between Pi-hole nodes

_GRAVITY_SYNC_DATA=""

_fetch_gravity_sync() {
    [ -n "$_GRAVITY_SYNC_DATA" ] && return
    local s0_timer s1_timer s0_exit s0_ts
    s0_timer=$(timed_ssh 5 slave0 "systemctl is-active gravity-sync.timer" 2>/dev/null || echo "inactive")
    s1_timer=$(timed_ssh 5 slave1 "systemctl is-active gravity-sync.timer" 2>/dev/null || echo "inactive")
    s0_exit=$(timed_ssh 5 slave0 "systemctl show gravity-sync.service -p ExecMainStatus --value" 2>/dev/null | tr -d '[:space:]')
    s0_ts=$(timed_ssh 5 slave0 "systemctl show gravity-sync.service -p ExecMainExitTimestamp --value" 2>/dev/null)
    local age_seconds="-1"
    if [[ -n "$s0_ts" && "$s0_ts" != *"n/a"* ]]; then
        local ts_epoch
        ts_epoch=$(date -d "$s0_ts" +%s 2>/dev/null || echo "0")
        [ "$ts_epoch" -gt 0 ] && age_seconds=$(( $(date +%s) - ts_epoch ))
    fi
    : "${s0_exit:=unknown}"
    _GRAVITY_SYNC_DATA="${s0_timer} ${s1_timer} ${s0_exit} ${age_seconds}"
}

check_gravity_sync() {
    _fetch_gravity_sync
    local s0_timer s1_timer s0_exit age_seconds
    read -r s0_timer s1_timer s0_exit age_seconds <<< "$_GRAVITY_SYNC_DATA"

    if [[ "$s0_timer" != "active" && "$s1_timer" != "active" ]]; then
        check_service "gravity-sync" "down" "Timer inactive on both nodes"
        return
    fi
    if [[ "$s0_timer" != "active" || "$s1_timer" != "active" ]]; then
        local down_node="slave0"
        [[ "$s0_timer" == "active" ]] && down_node="slave1"
        check_service "gravity-sync" "degraded" "${down_node} timer inactive"
        return
    fi
    if [[ "$s0_exit" != "0" && "$s0_exit" != "unknown" ]]; then
        check_service "gravity-sync" "degraded" "Last run failed (exit $s0_exit)"
        return
    fi
    if [[ "$age_seconds" -gt 1800 ]]; then
        check_service "gravity-sync" "degraded" "Last run $(( age_seconds / 60 ))m ago"
        return
    fi
    check_service "gravity-sync" "up"
}
