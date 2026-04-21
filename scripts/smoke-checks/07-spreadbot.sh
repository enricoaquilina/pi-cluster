#!/bin/bash
# Checks: Spreadbot (ClawHub skill inside gateway container on heavy)

check_spreadbot() {
    # Skip if gateway container isn't running — check_openclaw_gateway already reports that
    if ! timed_ssh 8 ${HEAVY_HOST} \
        "docker ps --filter 'name=openclaw-openclaw-gateway-1' --format '{{.Names}}' 2>/dev/null | grep -q gateway" \
        2>/dev/null; then
        return
    fi
    local paused_count
    paused_count=$(timed_ssh 8 ${HEAVY_HOST} \
        "docker logs openclaw-openclaw-gateway-1 --since 30m 2>&1 | grep -ci 'health.pause\|consecutive.*cancelled\|paused.*consecutive'" \
        2>/dev/null || echo "0")
    paused_count=$(echo "$paused_count" | tr -d '[:space:]')
    if [ "${paused_count:-0}" -gt 0 ]; then
        check_service "spreadbot" "degraded" "Health pause detected ($paused_count log lines in last 30m)"
    else
        check_service "spreadbot" "up"
    fi
}
