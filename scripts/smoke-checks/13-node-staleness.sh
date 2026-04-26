#!/bin/bash
# Checks: Node stats push freshness — detects dead push agents
# Catches: push agent dies but cached stats still show connected=true

_NODE_STALENESS_DATA=""
NODE_STALENESS_THRESHOLD="${NODE_STALENESS_THRESHOLD:-300}"

_fetch_node_staleness() {
    [ -n "$_NODE_STALENESS_DATA" ] && return
    local raw
    raw=$(curl -sf --max-time 5 "http://${HEAVY_IP}:8520/nodes" 2>/dev/null)
    if [ -z "$raw" ]; then
        _NODE_STALENESS_DATA="api_unreachable"
        return
    fi
    local parsed
    parsed=$(echo "$raw" | python3 -c "
import json, sys, time
data = json.load(sys.stdin)
now = time.time()
for n in data.get('nodes', []):
    name = n.get('name', 'unknown')
    push_ts = n.get('push_ts', 0)
    age = int(now - push_ts) if push_ts > 0 else -1
    print(name, push_ts, age)
" 2>/dev/null)
    if [ -z "$parsed" ]; then
        _NODE_STALENESS_DATA="parse_error"
    else
        _NODE_STALENESS_DATA="$parsed"
    fi
}

_check_node_staleness() {
    local svc_name="$1" node_name="$2"
    _fetch_node_staleness

    case "$_NODE_STALENESS_DATA" in
        api_unreachable)
            check_service "$svc_name" "down" "Router API unreachable" ;;
        parse_error)
            check_service "$svc_name" "down" "Router API returned invalid data" ;;
        *)
            local line age
            line=$(echo "$_NODE_STALENESS_DATA" | grep "^${node_name} ")
            if [ -z "$line" ]; then
                check_service "$svc_name" "down" "Node not found in Router API response"
                return
            fi
            age=$(echo "$line" | awk '{print $3}')

            if [ "$age" = "-1" ]; then
                check_service "$svc_name" "down" "No push_ts recorded (agent never pushed)"
            elif [ "$age" -gt "$NODE_STALENESS_THRESHOLD" ]; then
                local age_min=$(( age / 60 ))
                check_service "$svc_name" "down" "Stats stale (${age_min}m old, threshold ${NODE_STALENESS_THRESHOLD}s)"
            else
                check_service "$svc_name" "up" "" "$age"
            fi
            ;;
    esac
}

check_node_stats_heavy()   { _check_node_staleness "node-stats-heavy" "heavy"; }
check_node_stats_control() { _check_node_staleness "node-stats-control" "control"; }
check_node_stats_build()   { _check_node_staleness "node-stats-build" "build"; }
check_node_stats_light()   { _check_node_staleness "node-stats-light" "light"; }
