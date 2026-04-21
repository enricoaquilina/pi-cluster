#!/bin/bash
# Checks: OpenClaw Gateway health + container memory pressure

check_openclaw_gateway() {
    if ! curl -sf --max-time 3 http://${HEAVY_IP}:18789/healthz >/dev/null 2>&1; then
        check_service "openclaw-gateway" "down" "Gateway unreachable on heavy"
        return
    fi
    local start_ms end_ms ms
    start_ms=$(date +%s%N)
    if curl -sf --max-time 5 http://${HEAVY_IP}:18789/healthz >/dev/null 2>&1; then
        end_ms=$(date +%s%N)
        ms=$(( (end_ms - start_ms) / 1000000 ))
        check_service "openclaw-gateway" "up" "" "$ms"
    else
        check_service "openclaw-gateway" "degraded" "HTTP health check failed"
    fi
}

check_gateway_memory() {
    local raw pct
    raw=$(timed_ssh 8 ${HEAVY_HOST} "docker stats --no-stream --format '{{.MemPerc}}' openclaw-openclaw-gateway-1" 2>/dev/null)
    if [[ -z "$raw" ]]; then
        check_service "openclaw-gateway-memory" "down" "Cannot read container memory stats"
        return
    fi
    pct=${raw%%%}  # strip trailing %
    pct=${pct%.*}  # truncate to integer
    if [[ "$pct" -gt 90 ]]; then
        check_service "openclaw-gateway-memory" "down" "Container memory at ${pct}% — OOM imminent"
    elif [[ "$pct" -gt 75 ]]; then
        check_service "openclaw-gateway-memory" "degraded" "Container memory at ${pct}%"
    else
        check_service "openclaw-gateway-memory" "up" "" "0"
    fi
}
