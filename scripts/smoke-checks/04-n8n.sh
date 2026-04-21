#!/bin/bash
# Checks: n8n production and staging instances

check_n8n_prod() {
    local start_ms end_ms ms
    start_ms=$(date +%s%N)
    if curl -sf --max-time 5 http://${HEAVY_IP}:5678/healthz >/dev/null 2>&1; then
        end_ms=$(date +%s%N)
        ms=$(( (end_ms - start_ms) / 1000000 ))
        check_service "n8n-production" "up" "" "$ms"
    else
        check_service "n8n-production" "down" "Health endpoint unreachable"
    fi
}

check_n8n_staging() {
    local start_ms end_ms ms
    start_ms=$(date +%s%N)
    if curl -sf --max-time 5 http://${HEAVY_IP}:5679/healthz >/dev/null 2>&1; then
        end_ms=$(date +%s%N)
        ms=$(( (end_ms - start_ms) / 1000000 ))
        check_service "n8n-staging" "up" "" "$ms"
    else
        check_service "n8n-staging" "down" "Health endpoint unreachable"
    fi
}
