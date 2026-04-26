#!/bin/bash
# Checks: Orphan services — detects services running on wrong hosts
# Catches: stale deployments (e.g. polybot on master when it should only run on heavy)

_ORPHAN_SERVICE_DATA=""

ORPHAN_FORBIDDEN="polymarket-bot polybot spreadbot"

_fetch_orphan_services() {
    [ -n "$_ORPHAN_SERVICE_DATA" ] && return
    local result=""

    for host in master slave0 slave1; do
        local check_cmd="for svc in ${ORPHAN_FORBIDDEN}; do "
        check_cmd+='u=$(systemctl list-units "${svc}*" --state=active --no-legend --plain 2>/dev/null | awk "{print \$1}"); '
        check_cmd+='[ -n "$u" ] && echo "FOUND $u"; '
        check_cmd+="done"

        local output
        output=$(timed_ssh 8 "$host" "$check_cmd" 2>/dev/null)
        local rc=$?

        if [ "$rc" -eq 124 ] || [ "$rc" -eq 255 ]; then
            result+="${host} ssh_unreachable -"$'\n'
        elif [ -n "$output" ]; then
            local units
            units=$(echo "$output" | sed 's/FOUND //g' | tr '\n' ',' | sed 's/,$//')
            result+="${host} running ${units}"$'\n'
        fi
    done

    [ -z "$result" ] && result="clean"
    _ORPHAN_SERVICE_DATA="$result"
}

check_orphan_services() {
    _fetch_orphan_services

    if [ "$_ORPHAN_SERVICE_DATA" = "clean" ]; then
        check_service "orphan-services" "up"
        return
    fi

    local has_orphan=false has_ssh_fail=false orphans="" ssh_fails=""

    while IFS= read -r line; do
        [ -z "$line" ] && continue
        local host status svc
        host=$(echo "$line" | awk '{print $1}')
        status=$(echo "$line" | awk '{print $2}')
        svc=$(echo "$line" | awk '{$1=$2=""; print}' | sed 's/^ *//')

        case "$status" in
            running)
                has_orphan=true
                orphans+="${host}:${svc} "
                ;;
            ssh_unreachable)
                has_ssh_fail=true
                ssh_fails+="${host} "
                ;;
        esac
    done <<< "$_ORPHAN_SERVICE_DATA"

    if $has_orphan; then
        check_service "orphan-services" "down" "Orphan services: ${orphans}"
    elif $has_ssh_fail; then
        check_service "orphan-services" "degraded" "Cannot check: SSH failed to ${ssh_fails}"
    else
        check_service "orphan-services" "up"
    fi
}
