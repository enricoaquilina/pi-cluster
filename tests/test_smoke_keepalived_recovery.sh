#!/bin/bash
# Tests for smoke-recovery/keepalived.sh
# R1-R7: Keepalived auto-recovery scenarios

set -uo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$TEST_DIR/.." && pwd)"

source "$REPO_DIR/scripts/lib/test-harness.sh"

echo "=== Keepalived Recovery Tests ==="

FAIL_COUNT_DIR=$(mktemp -d)
STATE_DIR=$(mktemp -d)
LOG_FILE="/dev/null"
NOW="2026-04-27T12:00:00+00:00"
TIMESTAMP=$(date +%s)

declare -A RESULTS RESPONSE_MS ERRORS

_CIRCUIT_BREAKER_RESULT=0
check_circuit_breaker() { return $_CIRCUIT_BREAKER_RESULT; }

_ALERTS=()
send_alert() { _ALERTS+=("$1"); }

check_service() {
    local name="$1" status="$2" error="${3:-}"
    RESULTS[$name]="$status"
    [[ -n "$error" ]] && ERRORS[$name]="$error"
}

declare -A _TIMED_SSH_RESPONSES
timed_ssh() {
    local timeout="$1" host="$2"
    shift 2
    local cmd="$*"
    local key="${host}:${cmd}"
    if [[ -n "${_TIMED_SSH_RESPONSES[$key]+x}" ]]; then
        echo "${_TIMED_SSH_RESPONSES[$key]}"
        return 0
    fi
    return 1
}

# stub sleep to speed up tests
sleep() { :; }

_reset() {
    rm -f "$FAIL_COUNT_DIR"/* "$STATE_DIR"/*
    _ALERTS=()
    _TIMED_SSH_RESPONSES=()
    _CIRCUIT_BREAKER_RESULT=0
    ERRORS=()
    TIMESTAMP=$(date +%s)
}

# ═══════════ R1: Split-brain recovery succeeds ═══════════

run_r1() {
    _reset
    echo "3" > "$FAIL_COUNT_DIR/keepalived.count"
    echo "down" > "$STATE_DIR/keepalived.status"
    ERRORS[keepalived]="SPLIT-BRAIN: VIP on both nodes"
    _TIMED_SSH_RESPONSES=(
        ["slave1:sudo systemctl restart keepalived"]=""
        ["slave1:systemctl is-active keepalived"]="active"
        ["slave0:ip -4 addr show eth0 | grep -c 192.168.0.53"]="1"
        ["slave1:ip -4 addr show eth0 | grep -c 192.168.0.53"]="0"
    )

    source "$REPO_DIR/scripts/smoke-recovery/keepalived.sh"

    local count
    count=$(cat "$FAIL_COUNT_DIR/keepalived.count" 2>/dev/null)
    local status
    status=$(cat "$STATE_DIR/keepalived.status" 2>/dev/null)

    [[ "$count" == "0" ]] && [[ "$status" == "up" ]] && \
        pass "R1: Split-brain recovery resets count and status" || \
        fail "R1: Split-brain recovery" "count=$count status=$status"
    [[ "${_ALERTS[*]}" == *"SUCCESS"*"Split-brain resolved"* ]] && \
        pass "R1: Success alert sent" || \
        fail "R1: Success alert" "got: ${_ALERTS[*]}"
}

# ═══════════ R2: Split-brain recovery fails ═══════════

run_r2() {
    _reset
    echo "3" > "$FAIL_COUNT_DIR/keepalived.count"
    echo "down" > "$STATE_DIR/keepalived.status"
    ERRORS[keepalived]="SPLIT-BRAIN: VIP on both nodes"
    _TIMED_SSH_RESPONSES=(
        ["slave1:sudo systemctl restart keepalived"]=""
        ["slave1:systemctl is-active keepalived"]="active"
        ["slave0:ip -4 addr show eth0 | grep -c 192.168.0.53"]="1"
        ["slave1:ip -4 addr show eth0 | grep -c 192.168.0.53"]="1"
    )

    source "$REPO_DIR/scripts/smoke-recovery/keepalived.sh"

    local count
    count=$(cat "$FAIL_COUNT_DIR/keepalived.count" 2>/dev/null)

    [[ "$count" == "3" ]] && \
        pass "R2: Split-brain failure keeps count" || \
        fail "R2: Count unchanged" "count=$count"
    [[ "${_ALERTS[*]}" == *"FAILED"*"Split-brain not resolved"* ]] && \
        pass "R2: Failure alert sent" || \
        fail "R2: Failure alert" "got: ${_ALERTS[*]}"
}

# ═══════════ R3: Both-down recovery succeeds ═══════════

run_r3() {
    _reset
    echo "3" > "$FAIL_COUNT_DIR/keepalived.count"
    echo "down" > "$STATE_DIR/keepalived.status"
    ERRORS[keepalived]="Both nodes keepalived down"
    _TIMED_SSH_RESPONSES=(
        ["slave0:sudo systemctl restart keepalived"]=""
        ["slave1:sudo systemctl restart keepalived"]=""
        ["slave0:systemctl is-active keepalived"]="active"
        ["slave1:systemctl is-active keepalived"]="active"
        ["slave0:ip -4 addr show eth0 | grep -c 192.168.0.53"]="1"
    )

    source "$REPO_DIR/scripts/smoke-recovery/keepalived.sh"

    local count
    count=$(cat "$FAIL_COUNT_DIR/keepalived.count" 2>/dev/null)
    local status
    status=$(cat "$STATE_DIR/keepalived.status" 2>/dev/null)

    [[ "$count" == "0" && "$status" == "up" ]] && \
        pass "R3: Both-down recovery succeeds" || \
        fail "R3: Both-down recovery" "count=$count status=$status"
    [[ "${_ALERTS[*]}" == *"SUCCESS"*"Both keepalived nodes restored"* ]] && \
        pass "R3: Success alert sent" || \
        fail "R3: Success alert" "got: ${_ALERTS[*]}"
}

# ═══════════ R4: One node degraded, recovery succeeds ═══════════

run_r4() {
    _reset
    echo "0" > "$FAIL_COUNT_DIR/keepalived.count"
    echo "degraded" > "$STATE_DIR/keepalived.status"
    echo "$(( TIMESTAMP - 960 ))" > "$STATE_DIR/keepalived.since"
    ERRORS[keepalived]="slave1 keepalived not running"
    _TIMED_SSH_RESPONSES=(
        ["slave1:sudo systemctl restart keepalived"]=""
        ["slave1:systemctl is-active keepalived"]="active"
    )

    source "$REPO_DIR/scripts/smoke-recovery/keepalived.sh"

    local status
    status=$(cat "$STATE_DIR/keepalived.status" 2>/dev/null)

    [[ "$status" == "up" ]] && \
        pass "R4: Single-node degraded recovery succeeds" || \
        fail "R4: Status should be up" "status=$status"
    [[ "${_ALERTS[*]}" == *"SUCCESS"*"slave1 keepalived restored"* ]] && \
        pass "R4: Alert names correct node" || \
        fail "R4: Alert names node" "got: ${_ALERTS[*]}"
}

# ═══════════ R5: Degraded but not persistent (no action) ═══════════

run_r5() {
    _reset
    echo "0" > "$FAIL_COUNT_DIR/keepalived.count"
    echo "degraded" > "$STATE_DIR/keepalived.status"
    echo "$(( TIMESTAMP - 120 ))" > "$STATE_DIR/keepalived.since"
    ERRORS[keepalived]="slave1 keepalived not running"

    source "$REPO_DIR/scripts/smoke-recovery/keepalived.sh"

    [[ ${#_ALERTS[@]} -eq 0 ]] && \
        pass "R5: No action for non-persistent degraded" || \
        fail "R5: Should not alert" "got: ${_ALERTS[*]}"
}

# ═══════════ R6: Circuit breaker blocks recovery ═══════════

run_r6() {
    _reset
    echo "3" > "$FAIL_COUNT_DIR/keepalived.count"
    echo "down" > "$STATE_DIR/keepalived.status"
    ERRORS[keepalived]="SPLIT-BRAIN: VIP on both nodes"
    _CIRCUIT_BREAKER_RESULT=1

    source "$REPO_DIR/scripts/smoke-recovery/keepalived.sh"

    [[ ${#_ALERTS[@]} -eq 0 ]] && \
        pass "R6: Circuit breaker blocks recovery" || \
        fail "R6: Should not alert" "got: ${_ALERTS[*]}"
}

# ═══════════ R7: VIP missing, recovery succeeds ═══════════

run_r7() {
    _reset
    echo "0" > "$FAIL_COUNT_DIR/keepalived.count"
    echo "degraded" > "$STATE_DIR/keepalived.status"
    echo "$(( TIMESTAMP - 960 ))" > "$STATE_DIR/keepalived.since"
    ERRORS[keepalived]="VIP 192.168.0.53 not found on either node"
    _TIMED_SSH_RESPONSES=(
        ["slave0:sudo systemctl restart keepalived"]=""
        ["slave1:sudo systemctl restart keepalived"]=""
        ["slave0:ip -4 addr show eth0 | grep -c 192.168.0.53"]="1"
        ["slave1:ip -4 addr show eth0 | grep -c 192.168.0.53"]="0"
    )

    source "$REPO_DIR/scripts/smoke-recovery/keepalived.sh"

    local status
    status=$(cat "$STATE_DIR/keepalived.status" 2>/dev/null)

    [[ "$status" == "up" ]] && \
        pass "R7: VIP missing recovery succeeds" || \
        fail "R7: Status should be up" "status=$status"
    [[ "${_ALERTS[*]}" == *"SUCCESS"*"VIP restored"* ]] && \
        pass "R7: VIP restored alert" || \
        fail "R7: VIP alert" "got: ${_ALERTS[*]}"
}

run_r1; run_r2; run_r3; run_r4; run_r5; run_r6; run_r7

rm -rf "$FAIL_COUNT_DIR" "$STATE_DIR"

test_summary
