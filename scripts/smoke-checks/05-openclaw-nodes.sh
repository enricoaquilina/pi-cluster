#!/bin/bash
# Checks: OpenClaw node connectivity (master, slave0, slave1, heavy) + Router API
# Two-layer verification: Router API status + systemd service status via SSH.
# Catches the case where stats agent reports "connected" but openclaw-node service is down.

_OPENCLAW_NODE_STATUS=""
_OPENCLAW_SERVICE_STATUS=""

_fetch_openclaw_node_status() {
    [ -n "$_OPENCLAW_NODE_STATUS" ] && return
    local raw parsed
    raw=$(curl -sf --max-time 5 "http://${HEAVY_IP}:8520/nodes" 2>/dev/null)
    if [ -z "$raw" ]; then
        _OPENCLAW_NODE_STATUS="api_unreachable"
        return
    fi
    parsed=$(echo "$raw" | python3 -c "
import json, sys
data = json.load(sys.stdin)
nodes = {n['name']: n.get('connected', False) for n in data.get('nodes', [])}
for name in ['control', 'build', 'light', 'heavy']:
    print(name, 'true' if nodes.get(name) else 'false')
" 2>/dev/null)
    if [ -z "$parsed" ]; then
        _OPENCLAW_NODE_STATUS="parse_error"
    else
        _OPENCLAW_NODE_STATUS="$parsed"
    fi
}

_node_ssh_host() {
    case "$1" in
        control) echo "master" ;;
        build)   echo "slave0" ;;
        light)   echo "slave1" ;;
        heavy)   echo "heavy" ;;
    esac
}

_verify_node_services() {
    [ -n "$_OPENCLAW_SERVICE_STATUS" ] && return
    local result="" node host active
    for node in control build light heavy; do
        host=$(_node_ssh_host "$node")
        if [ -z "$host" ]; then
            active=$(systemctl is-active openclaw-node 2>/dev/null) || true
        else
            active=$(timed_ssh 5 "$host" systemctl is-active openclaw-node 2>/dev/null) || true
        fi
        result+="${node} ${active:-unknown}"$'\n'
    done
    _OPENCLAW_SERVICE_STATUS="$result"
}

_check_openclaw_node() {
    local svc_name="$1" node_name="$2"
    _fetch_openclaw_node_status
    _verify_node_services

    case "$_OPENCLAW_NODE_STATUS" in
        api_unreachable) check_service "$svc_name" "down" "Router API unreachable" ;;
        parse_error)     check_service "$svc_name" "down" "Router API returned invalid JSON" ;;
        *)
            local api_connected=false svc_active
            echo "$_OPENCLAW_NODE_STATUS" | grep -q "^${node_name} true" && api_connected=true
            svc_active=$(echo "$_OPENCLAW_SERVICE_STATUS" | grep "^${node_name} " | awk '{print $2}')

            if $api_connected; then
                case "$svc_active" in
                    inactive|failed|dead)
                        check_service "$svc_name" "degraded" "Router API connected but openclaw-node service ${svc_active}" ;;
                    *)
                        check_service "$svc_name" "up" ;;
                esac
            else
                check_service "$svc_name" "down" "Node not connected to gateway"
            fi
            ;;
    esac
}

check_openclaw_master() { _check_openclaw_node "openclaw-master" "control"; }
check_openclaw_slave0() { _check_openclaw_node "openclaw-slave0" "build"; }
check_openclaw_slave1() { _check_openclaw_node "openclaw-slave1" "light"; }
check_openclaw_heavy()  { _check_openclaw_node "openclaw-heavy" "heavy"; }

check_router_api() {
    if curl -sf --max-time 5 http://${HEAVY_IP}:8520/health >/dev/null 2>&1; then
        check_service "router-api" "up"
    else
        check_service "router-api" "down" "Router API unreachable"
    fi
}
