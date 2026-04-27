#!/bin/bash
# Checks: End-to-end heartbeat pipeline (node-agent → stats-collector → mc-feed → MC API)
# Catches: pipeline-level failures that component checks miss (the Apr 26-27 outage scenario)

HEARTBEAT_CANARY_THRESHOLD="${HEARTBEAT_CANARY_THRESHOLD:-300}"
_HEARTBEAT_CANARY_DATA=""

_fetch_heartbeat_canary() {
    [ -n "$_HEARTBEAT_CANARY_DATA" ] && return
    local raw
    raw=$(curl -sf --max-time 5 "${MISSION_CONTROL_API}/nodes" \
        -H "x-api-key: ${API_KEY}" 2>/dev/null)
    if [ -z "$raw" ]; then
        _HEARTBEAT_CANARY_DATA="api_unreachable"
        return
    fi
    local parsed
    parsed=$(python3 -c "
import json, sys
from datetime import datetime, timezone
data = json.load(sys.stdin)
nodes = data.get('nodes', data) if isinstance(data, dict) else data
now = datetime.now(timezone.utc)
for n in nodes:
    name = n.get('name', 'unknown')
    hb = n.get('last_heartbeat', '')
    if hb:
        try:
            hb_dt = datetime.fromisoformat(hb.replace('Z', '+00:00'))
            age = int((now - hb_dt).total_seconds())
        except Exception:
            age = -1
    else:
        age = -1
    print(name, age)
" <<< "$raw" 2>/dev/null)
    _HEARTBEAT_CANARY_DATA="${parsed:-parse_error}"
}

check_heartbeat_canary() {
    _fetch_heartbeat_canary
    case "$_HEARTBEAT_CANARY_DATA" in
        api_unreachable)
            check_service "heartbeat-canary" "down" "MC API unreachable"
            return ;;
        parse_error)
            check_service "heartbeat-canary" "down" "MC API returned unparseable data"
            return ;;
    esac

    local stale_nodes="" total=0 stale=0
    while read -r name age; do
        [ -z "$name" ] && continue
        total=$((total + 1))
        if [ "$age" = "-1" ]; then
            stale=$((stale + 1))
            stale_nodes="${stale_nodes}${name}(never) "
        elif [ "$age" -gt "$HEARTBEAT_CANARY_THRESHOLD" ]; then
            stale=$((stale + 1))
            local age_min=$(( age / 60 ))
            stale_nodes="${stale_nodes}${name}(${age_min}m) "
        fi
    done <<< "$_HEARTBEAT_CANARY_DATA"

    if [ "$total" -eq 0 ]; then
        check_service "heartbeat-canary" "down" "No nodes found in MC"
    elif [ "$stale" -eq "$total" ]; then
        check_service "heartbeat-canary" "down" "All nodes stale: ${stale_nodes}"
    elif [ "$stale" -gt 0 ]; then
        check_service "heartbeat-canary" "degraded" "Stale: ${stale_nodes}"
    else
        check_service "heartbeat-canary" "up"
    fi
}
