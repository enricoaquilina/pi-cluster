#!/bin/bash
# Tests for scripts/smoke-checks/17-heartbeat-canary.sh
#
# Validates end-to-end heartbeat pipeline detection:
#   C1: All nodes fresh heartbeats → UP
#   C2: One node stale → DEGRADED
#   C3: All nodes stale → DOWN
#   C4: No heartbeat field → DOWN
#   C5: MC API unreachable → DOWN
#   C6: Zero nodes in response → DOWN

set -uo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$TEST_DIR/.." && pwd)"

source "$REPO_DIR/scripts/lib/test-harness.sh"

echo "=== Heartbeat Canary ==="

HEAVY_IP="127.0.0.1"
MISSION_CONTROL_API="http://127.0.0.1:99999/api"
API_KEY="test"
declare -A RESULTS RESPONSE_MS ERRORS

check_service() {
    local name="$1" status="$2" error="${3:-}" ms="${4:-}"
    RESULTS[$name]="$status"
    [[ -n "$error" ]] && ERRORS[$name]="$error"
    [[ -n "$ms" ]] && RESPONSE_MS[$name]="$ms"
}

source "$REPO_DIR/scripts/smoke-checks/17-heartbeat-canary.sh"

_reset() {
    RESULTS=()
    RESPONSE_MS=()
    ERRORS=()
    _HEARTBEAT_CANARY_DATA=""
}

_now_iso() {
    python3 -c "from datetime import datetime, timezone, timedelta; print((datetime.now(timezone.utc) - timedelta(seconds=${1:-0})).isoformat())"
}

# -- C1: All nodes fresh → UP --
run_c1() {
    _reset
    local hb_now
    hb_now=$(_now_iso 10)
    _HEARTBEAT_CANARY_DATA="heavy 10
master 10
slave0 10
slave1 10"

    check_heartbeat_canary

    [ "${RESULTS[heartbeat-canary]}" = "up" ] && \
        pass "C1: all nodes fresh → UP" || \
        fail "C1: all nodes fresh → UP" "got: ${RESULTS[heartbeat-canary]:-unset}"
}

# -- C2: One node stale → DEGRADED --
run_c2() {
    _reset
    _HEARTBEAT_CANARY_DATA="heavy 10
master 10
slave0 600
slave1 10"

    check_heartbeat_canary

    [ "${RESULTS[heartbeat-canary]}" = "degraded" ] && \
        pass "C2: one node stale → DEGRADED" || \
        fail "C2: one node stale → DEGRADED" "got: ${RESULTS[heartbeat-canary]:-unset}"

    [[ "${ERRORS[heartbeat-canary]:-}" == *"slave0"* ]] && \
        pass "C2: stale node named in error" || \
        fail "C2: stale node named in error" "got: ${ERRORS[heartbeat-canary]:-none}"
}

# -- C3: All nodes stale → DOWN --
run_c3() {
    _reset
    _HEARTBEAT_CANARY_DATA="heavy 600
master 600
slave0 600
slave1 600"

    check_heartbeat_canary

    [ "${RESULTS[heartbeat-canary]}" = "down" ] && \
        pass "C3: all nodes stale → DOWN" || \
        fail "C3: all nodes stale → DOWN" "got: ${RESULTS[heartbeat-canary]:-unset}"
}

# -- C4: No heartbeat (age=-1) → DOWN --
run_c4() {
    _reset
    _HEARTBEAT_CANARY_DATA="heavy -1
master -1
slave0 -1
slave1 -1"

    check_heartbeat_canary

    [ "${RESULTS[heartbeat-canary]}" = "down" ] && \
        pass "C4: no heartbeat field → DOWN" || \
        fail "C4: no heartbeat field → DOWN" "got: ${RESULTS[heartbeat-canary]:-unset}"
}

# -- C5: MC API unreachable → DOWN --
run_c5() {
    _reset
    _HEARTBEAT_CANARY_DATA="api_unreachable"

    check_heartbeat_canary

    [ "${RESULTS[heartbeat-canary]}" = "down" ] && \
        pass "C5: MC API unreachable → DOWN" || \
        fail "C5: MC API unreachable → DOWN" "got: ${RESULTS[heartbeat-canary]:-unset}"
}

# -- C6: Zero nodes → DOWN --
run_c6() {
    _reset
    _HEARTBEAT_CANARY_DATA=""
    check_heartbeat_canary

    [ "${RESULTS[heartbeat-canary]}" = "down" ] && \
        pass "C6: zero nodes → DOWN" || \
        fail "C6: zero nodes → DOWN" "got: ${RESULTS[heartbeat-canary]:-unset}"
}

run_c1
run_c2
run_c3
run_c4
run_c5
run_c6

test_summary
