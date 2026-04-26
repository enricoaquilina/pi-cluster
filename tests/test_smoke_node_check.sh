#!/bin/bash
# Tests for scripts/smoke-checks/05-openclaw-nodes.sh
#
# Validates two-layer monitoring: Router API status + systemd service check.
# Catches the scenario where openclaw-node is down but stats agent reports healthy.
#
#   T1: Router API connected + service active → UP
#   T2: Router API connected + service dead → DEGRADED
#   T3: Router API disconnected → DOWN
#   T4: Router API unreachable → DOWN
#   T5: SSH timeout (unknown service) → trusts Router API (UP)

set -uo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$TEST_DIR/.." && pwd)"
CHECK_FILE="$REPO_DIR/scripts/smoke-checks/05-openclaw-nodes.sh"

source "$REPO_DIR/scripts/lib/test-harness.sh"

echo "=== 05-openclaw-nodes.sh ==="

HEAVY_IP="127.0.0.1"
declare -A RESULTS RESPONSE_MS ERRORS

check_service() {
    local name="$1" status="$2" error="${3:-}"
    RESULTS[$name]="$status"
    [[ -n "$error" ]] && ERRORS[$name]="$error"
}

timed_ssh() { :; }

source "$CHECK_FILE"

_reset() {
    RESULTS=()
    RESPONSE_MS=()
    ERRORS=()
    _OPENCLAW_NODE_STATUS=""
    _OPENCLAW_SERVICE_STATUS=""
}

# -- T1: Router API connected + service active → UP --
run_t1() {
    _reset
    _OPENCLAW_NODE_STATUS="control true
build true
light true
heavy true"
    _OPENCLAW_SERVICE_STATUS="control active
build active
light active
heavy active"

    check_openclaw_master
    check_openclaw_heavy

    if [ "${RESULTS[openclaw-master]}" = "up" ]; then
        pass "T1: master UP when API connected + service active"
    else
        fail "T1: master UP when API connected + service active" "got: ${RESULTS[openclaw-master]:-unset}"
    fi
    if [ "${RESULTS[openclaw-heavy]}" = "up" ]; then
        pass "T1: heavy UP when API connected + service active"
    else
        fail "T1: heavy UP when API connected + service active" "got: ${RESULTS[openclaw-heavy]:-unset}"
    fi
}

# -- T2: Router API connected + service dead → DEGRADED --
run_t2() {
    _reset
    _OPENCLAW_NODE_STATUS="control true
build true
light true
heavy true"
    _OPENCLAW_SERVICE_STATUS="control inactive
build active
light active
heavy failed"

    check_openclaw_master
    check_openclaw_heavy

    if [ "${RESULTS[openclaw-master]}" = "degraded" ]; then
        pass "T2: master DEGRADED when API connected but service inactive"
    else
        fail "T2: master DEGRADED when API connected but service inactive" "got: ${RESULTS[openclaw-master]:-unset}"
    fi
    if [ "${RESULTS[openclaw-heavy]}" = "degraded" ]; then
        pass "T2: heavy DEGRADED when API connected but service failed"
    else
        fail "T2: heavy DEGRADED when API connected but service failed" "got: ${RESULTS[openclaw-heavy]:-unset}"
    fi
}

# -- T3: Router API disconnected → DOWN --
run_t3() {
    _reset
    _OPENCLAW_NODE_STATUS="control false
build false
light true
heavy true"
    _OPENCLAW_SERVICE_STATUS="control active
build active
light active
heavy active"

    check_openclaw_master
    check_openclaw_slave0

    if [ "${RESULTS[openclaw-master]}" = "down" ]; then
        pass "T3: master DOWN when API shows disconnected"
    else
        fail "T3: master DOWN when API shows disconnected" "got: ${RESULTS[openclaw-master]:-unset}"
    fi
    if [ "${RESULTS[openclaw-slave0]}" = "down" ]; then
        pass "T3: slave0 DOWN when API shows disconnected"
    else
        fail "T3: slave0 DOWN when API shows disconnected" "got: ${RESULTS[openclaw-slave0]:-unset}"
    fi
}

# -- T4: Router API unreachable → DOWN --
run_t4() {
    _reset
    _OPENCLAW_NODE_STATUS="api_unreachable"
    _OPENCLAW_SERVICE_STATUS="control active
build active
light active
heavy active"

    check_openclaw_master
    check_openclaw_heavy

    if [ "${RESULTS[openclaw-master]}" = "down" ]; then
        pass "T4: master DOWN when API unreachable"
    else
        fail "T4: master DOWN when API unreachable" "got: ${RESULTS[openclaw-master]:-unset}"
    fi
    if [ "${RESULTS[openclaw-heavy]}" = "down" ]; then
        pass "T4: heavy DOWN when API unreachable"
    else
        fail "T4: heavy DOWN when API unreachable" "got: ${RESULTS[openclaw-heavy]:-unset}"
    fi
}

# -- T5: SSH timeout → falls back to Router API only (UP) --
run_t5() {
    _reset
    _OPENCLAW_NODE_STATUS="control true
build true
light true
heavy true"
    _OPENCLAW_SERVICE_STATUS="control unknown
build unknown
light unknown
heavy unknown"

    check_openclaw_master
    check_openclaw_heavy

    if [ "${RESULTS[openclaw-master]}" = "up" ]; then
        pass "T5: master UP when SSH unknown (trusts Router API)"
    else
        fail "T5: master UP when SSH unknown (trusts Router API)" "got: ${RESULTS[openclaw-master]:-unset}"
    fi
    if [ "${RESULTS[openclaw-heavy]}" = "up" ]; then
        pass "T5: heavy UP when SSH unknown (trusts Router API)"
    else
        fail "T5: heavy UP when SSH unknown (trusts Router API)" "got: ${RESULTS[openclaw-heavy]:-unset}"
    fi
}

run_t1
run_t2
run_t3
run_t4
run_t5

test_summary
