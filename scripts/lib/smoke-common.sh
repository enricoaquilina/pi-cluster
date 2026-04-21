#!/bin/bash
# shellcheck disable=SC2034
# smoke-common.sh — shared variables, functions, and globals for smoke tests
# Sourced by system-smoke-test.sh before check files

# ── Shared Variables ──────────────────────────────────────────────────────────

HEAVY_IP="${HEAVY_IP:-192.168.0.5}"
HEAVY_HOST="${HEAVY_HOST:-heavy}"
MISSION_CONTROL_API="http://${HEAVY_IP}:8000/api"
API_KEY="${MC_API_KEY:-$(grep '^API_KEY=' /mnt/external/mission-control/.env 2>/dev/null | cut -d= -f2)}"
ALERT_SCRIPT="/usr/local/bin/cluster-alert.sh"
STATE_DIR="/var/run/cluster-health/services"
FAIL_COUNT_DIR="/var/run/cluster-health/fail-counts"
RESULTS_FILE="/tmp/smoke-test-latest.json"
LOG_FILE="/tmp/smoke-test.log"
ALERT_INTERVAL=600  # 10 minutes

RESTART_COUNT_FILE="/var/run/cluster-health/restart-count"
CIRCUIT_LOCK_FILE="/var/run/cluster-health/restart-count.lock"
MAX_RESTARTS_PER_HOUR=3

mkdir -p "$STATE_DIR" "$FAIL_COUNT_DIR"

# ── SSH Helpers ───────────────────────────────────────────────────────────────

_ssh() { ssh -o ConnectTimeout=3 -o BatchMode=yes "$@"; }
# NOTE: Do NOT use `timeout X _ssh` — timeout uses execvp() which only finds
# external commands, not shell functions (silent exit 127). Use timed_ssh instead.
timed_ssh() { local t="$1"; shift; timeout "$t" ssh -o ConnectTimeout=3 -o BatchMode=yes "$@"; }

# ── Colors ────────────────────────────────────────────────────────────────────

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; NC='\033[0m'; BOLD='\033[1m'

# ── Timestamps ────────────────────────────────────────────────────────────────

NOW=$(date -Iseconds)
TIMESTAMP=$(date +%s)

# ── Global Associative Arrays ─────────────────────────────────────────────────

declare -A RESULTS
declare -A RESPONSE_MS
declare -A ERRORS

# ── Core Check Function ──────────────────────────────────────────────────────

check_service() {
    local name="$1" status="$2" error="${3:-}" ms="${4:-}"
    RESULTS[$name]="$status"
    [[ -n "$error" ]] && ERRORS[$name]="$error"
    [[ -n "$ms" ]] && RESPONSE_MS[$name]="$ms"
}

# ── External Connectivity Meta-Check ─────────────────────────────────────────

HOST_DNS_OK=true
if ! dig +short google.com @8.8.8.8 +time=3 >/dev/null 2>&1; then
    HOST_DNS_OK=false
fi

# ── Alert Functions ───────────────────────────────────────────────────────────

send_alert() {
    local msg="$1"
    bash "$ALERT_SCRIPT" "$msg" 2>/dev/null || true
}

post_alert_to_api() {
    local svc="$1" status="$2" msg="$3" downtime="${4:-}"
    local json
    json='{"service":"'"$svc"'","status":"'"$status"'","message":"'"$(echo "$msg" | sed 's/"/\\"/g')"'"'
    [[ -n "$downtime" ]] && json+=',"downtime_seconds":'"$downtime"
    json+='}'
    curl -sf --max-time 10 -X POST "${MISSION_CONTROL_API}/services/alert" \
        -H "x-api-key: ${API_KEY}" \
        -H "Content-Type: application/json" \
        -d "$json" >/dev/null 2>&1 || true
}

# ── Circuit Breaker ───────────────────────────────────────────────────────────

# Cap auto-recovery restarts per hour (flock prevents TOCTOU race)
check_circuit_breaker() {
    (
        flock -x 200
        if [ -f "$RESTART_COUNT_FILE" ]; then
            AGE=$(( $(date +%s) - $(stat -c %Y "$RESTART_COUNT_FILE") ))
            [ "$AGE" -gt 3600 ] && echo 0 > "$RESTART_COUNT_FILE"
        fi
        COUNT=$(cat "$RESTART_COUNT_FILE" 2>/dev/null || echo 0)
        if [ "$COUNT" -ge "$MAX_RESTARTS_PER_HOUR" ]; then
            send_alert "Circuit breaker: $COUNT restarts this hour, skipping auto-recovery"
            exit 1
        fi
        echo $((COUNT + 1)) > "$RESTART_COUNT_FILE"
        exit 0
    ) 200>"$CIRCUIT_LOCK_FILE"
    return $?
}
