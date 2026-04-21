#!/bin/bash
# Checks: OpenClaw node connectivity (master, slave0, slave1, heavy) + Router API
# Uses cached node status from _fetch_openclaw_node_status()

_OPENCLAW_NODE_STATUS=""

_fetch_openclaw_node_status() {
    [ -n "$_OPENCLAW_NODE_STATUS" ] && return  # idempotent
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

check_openclaw_master() {
    case "$_OPENCLAW_NODE_STATUS" in
        api_unreachable) check_service "openclaw-master" "down" "Router API unreachable" ;;
        parse_error)     check_service "openclaw-master" "down" "Router API returned invalid JSON" ;;
        *) if echo "$_OPENCLAW_NODE_STATUS" | grep -q "^control true"; then
               check_service "openclaw-master" "up"
           else
               check_service "openclaw-master" "down" "Node not connected to gateway"
           fi ;;
    esac
}

check_openclaw_slave0() {
    case "$_OPENCLAW_NODE_STATUS" in
        api_unreachable) check_service "openclaw-slave0" "down" "Router API unreachable" ;;
        parse_error)     check_service "openclaw-slave0" "down" "Router API returned invalid JSON" ;;
        *) if echo "$_OPENCLAW_NODE_STATUS" | grep -q "^build true"; then
               check_service "openclaw-slave0" "up"
           else
               check_service "openclaw-slave0" "down" "Node not connected to gateway"
           fi ;;
    esac
}

check_openclaw_slave1() {
    case "$_OPENCLAW_NODE_STATUS" in
        api_unreachable) check_service "openclaw-slave1" "down" "Router API unreachable" ;;
        parse_error)     check_service "openclaw-slave1" "down" "Router API returned invalid JSON" ;;
        *) if echo "$_OPENCLAW_NODE_STATUS" | grep -q "^light true"; then
               check_service "openclaw-slave1" "up"
           else
               check_service "openclaw-slave1" "down" "Node not connected to gateway"
           fi ;;
    esac
}

check_openclaw_heavy() {
    case "$_OPENCLAW_NODE_STATUS" in
        api_unreachable) check_service "openclaw-heavy" "down" "Router API unreachable" ;;
        parse_error)     check_service "openclaw-heavy" "down" "Router API returned invalid JSON" ;;
        *) if echo "$_OPENCLAW_NODE_STATUS" | grep -q "^heavy true"; then
               check_service "openclaw-heavy" "up"
           else
               check_service "openclaw-heavy" "down" "Node not connected to gateway"
           fi ;;
    esac
}

check_router_api() {
    if curl -sf --max-time 5 http://${HEAVY_IP}:8520/health >/dev/null 2>&1; then
        check_service "router-api" "up"
    else
        check_service "router-api" "down" "Router API unreachable"
    fi
}
