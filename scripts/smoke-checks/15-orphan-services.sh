#!/bin/bash
# Checks: Orphan services, dual systemd layer audit, service inventory
# Catches: stale deployments, user-level systemd masking, missing expected services

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

# ── Dual Systemd Layer Audit ─────────────────────────────────────────────────

_SYSTEMD_LAYER_DATA=""

LAYER_FORBIDDEN_PATTERNS="polybot|spreadbot|openclaw-node"
LAYER_ALLOWED_USER="openclaw-restart-count-alerter|openclaw-config-canary|mc-kiosk"

_fetch_systemd_layer_data() {
    [ -n "$_SYSTEMD_LAYER_DATA" ] && return
    local result=""

    for host in heavy master slave0 slave1; do
        local output
        local find_cmd="find ~/.config/systemd/user/ -maxdepth 1 -type f -regextype posix-extended -iregex '.*(${LAYER_FORBIDDEN_PATTERNS}).*' ! -iregex '.*(${LAYER_ALLOWED_USER}).*' -printf '%f\n' 2>/dev/null"
        if [ "$host" = "heavy" ]; then
            output=$(eval "$find_cmd")
        else
            output=$(timed_ssh 5 "$host" "$find_cmd" 2>/dev/null)
            local rc=$?
            if [ "$rc" -eq 124 ] || [ "$rc" -eq 255 ]; then
                result+="${host} ssh_unreachable -"$'\n'
                continue
            fi
        fi

        if [ -n "$output" ]; then
            local files
            files=$(echo "$output" | tr '\n' ',' | sed 's/,$//')
            result+="${host} found ${files}"$'\n'
        fi
    done

    [ -z "$result" ] && result="clean"
    _SYSTEMD_LAYER_DATA="$result"
}

check_systemd_layer_audit() {
    _fetch_systemd_layer_data

    if [ "$_SYSTEMD_LAYER_DATA" = "clean" ]; then
        check_service "systemd-layer-audit" "up"
        return
    fi

    local has_forbidden=false has_ssh_fail=false forbidden="" ssh_fails=""

    while IFS= read -r line; do
        [ -z "$line" ] && continue
        local host status detail
        host=$(echo "$line" | awk '{print $1}')
        status=$(echo "$line" | awk '{print $2}')
        detail=$(echo "$line" | awk '{$1=$2=""; print}' | sed 's/^ *//')

        case "$status" in
            found)
                has_forbidden=true
                forbidden+="${host}:${detail} "
                ;;
            ssh_unreachable)
                has_ssh_fail=true
                ssh_fails+="${host} "
                ;;
        esac
    done <<< "$_SYSTEMD_LAYER_DATA"

    if $has_forbidden; then
        check_service "systemd-layer-audit" "degraded" "User-level service files: ${forbidden}"
    elif $has_ssh_fail; then
        check_service "systemd-layer-audit" "degraded" "Cannot check: SSH failed to ${ssh_fails}"
    else
        check_service "systemd-layer-audit" "up"
    fi
}

# ── Service Inventory ────────────────────────────────────────────────────────

_SERVICE_INVENTORY_DATA=""

declare -A EXPECTED_SERVICES=(
    [heavy]="openclaw-node openclaw-router-api"
    [master]="openclaw-node"
    [slave0]="openclaw-node"
    [slave1]="openclaw-node"
)

_fetch_service_inventory() {
    [ -n "$_SERVICE_INVENTORY_DATA" ] && return
    local result=""

    for host in heavy master slave0 slave1; do
        local expected="${EXPECTED_SERVICES[$host]}"
        [ -z "$expected" ] && continue

        for svc in $expected; do
            local status
            if [ "$host" = "heavy" ]; then
                status=$(systemctl is-active "$svc" 2>/dev/null || echo "unknown")
            else
                status=$(timed_ssh 5 "$host" "systemctl is-active $svc 2>/dev/null || echo unknown" 2>/dev/null)
                local rc=$?
                if [ "$rc" -eq 124 ] || [ "$rc" -eq 255 ]; then
                    result+="${host} ${svc} ssh_failed"$'\n'
                    continue
                fi
            fi
            status=$(echo "$status" | tr -d '[:space:]')
            result+="${host} ${svc} ${status}"$'\n'
        done
    done

    [ -z "$result" ] && result="all_ok"
    _SERVICE_INVENTORY_DATA="${result%$'\n'}"
}

check_service_inventory() {
    _fetch_service_inventory

    if [ "$_SERVICE_INVENTORY_DATA" = "all_ok" ]; then
        check_service "service-inventory" "up"
        return
    fi

    local missing="" ssh_fails="" all_active=true

    while IFS= read -r line; do
        [ -z "$line" ] && continue
        local host svc status
        host=$(echo "$line" | awk '{print $1}')
        svc=$(echo "$line" | awk '{print $2}')
        status=$(echo "$line" | awk '{print $3}')

        case "$status" in
            active) ;;
            ssh_failed)
                ssh_fails+="${host}:${svc} "
                all_active=false
                ;;
            *)
                missing+="${host}:${svc}(${status}) "
                all_active=false
                ;;
        esac
    done <<< "$_SERVICE_INVENTORY_DATA"

    if $all_active; then
        check_service "service-inventory" "up"
    elif [ -n "$missing" ]; then
        check_service "service-inventory" "down" "Missing/inactive: ${missing}"
    else
        check_service "service-inventory" "degraded" "Cannot check: SSH failed to ${ssh_fails}"
    fi
}
