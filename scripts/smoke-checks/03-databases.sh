#!/bin/bash
# Checks: Mission Control API, PostgreSQL, MongoDB

check_mc_api() {
    local start_ms end_ms ms
    start_ms=$(date +%s%N)
    if _ssh heavy "curl -sf --max-time 3 http://localhost:8000/health" >/dev/null 2>&1; then
        end_ms=$(date +%s%N)
        ms=$(( (end_ms - start_ms) / 1000000 ))
        check_service "mission-control-api" "up" "" "$ms"
    else
        check_service "mission-control-api" "down" "Health endpoint unreachable"
    fi
}

check_postgres() {
    if timed_ssh 8 ${HEAVY_HOST} "docker exec mission-control-db pg_isready -U missioncontrol" >/dev/null 2>&1; then
        check_service "postgresql" "up"
    else
        check_service "postgresql" "down" "pg_isready failed"
    fi
}

check_mongodb() {
    if timed_ssh 8 ${HEAVY_HOST} 'docker exec mongodb mongosh --quiet --eval "db.runCommand({ping:1}).ok"' >/dev/null 2>&1; then
        check_service "mongodb" "up"
    else
        check_service "mongodb" "down" "mongosh ping failed"
    fi
}
