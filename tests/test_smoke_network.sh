#!/bin/bash
# Tests for smoke-checks/09-network.sh, 10-storage.sh, 18-gravity-sync.sh
#
# H1-H4: Keepalived VIP ownership + split-brain detection
# N1-N7: NFS mount/server health check (local + remote client)
# G1-G6: Gravity-sync timer + service health

set -uo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$TEST_DIR/.." && pwd)"

source "$REPO_DIR/scripts/lib/test-harness.sh"

echo "=== Network + NFS Smoke Checks ==="

HEAVY_IP="127.0.0.1"
declare -A RESULTS RESPONSE_MS ERRORS

check_service() {
    local name="$1" status="$2" error="${3:-}" ms="${4:-}"
    RESULTS[$name]="$status"
    [[ -n "$error" ]] && ERRORS[$name]="$error"
    [[ -n "$ms" ]] && RESPONSE_MS[$name]="$ms"
}

# Injectable timed_ssh — tests set _TIMED_SSH_RESPONSES
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
    echo "inactive"
    return 1
}

source "$REPO_DIR/scripts/smoke-checks/09-network.sh"
source "$REPO_DIR/scripts/smoke-checks/10-storage.sh"
source "$REPO_DIR/scripts/smoke-checks/18-gravity-sync.sh"

_reset() {
    RESULTS=()
    RESPONSE_MS=()
    ERRORS=()
    _TIMED_SSH_RESPONSES=()
    _NFS_OWNERSHIP_DATA=""
    _GRAVITY_SYNC_DATA=""
}

# ═══════════ Check H: Keepalived VIP ═══════════

run_h1() {
    _reset
    _TIMED_SSH_RESPONSES=(
        ["slave0:systemctl is-active keepalived"]="active"
        ["slave1:systemctl is-active keepalived"]="active"
        ["slave0:ip -4 addr show eth0 | grep -c 192.168.0.53"]="1"
        ["slave1:ip -4 addr show eth0 | grep -c 192.168.0.53"]="0"
    )

    check_keepalived

    [ "${RESULTS[keepalived]}" = "up" ] && \
        pass "H1: UP when both active, VIP on slave0" || \
        fail "H1: UP when VIP on slave0" "got: ${RESULTS[keepalived]:-unset}"
}

run_h2() {
    _reset
    _TIMED_SSH_RESPONSES=(
        ["slave0:systemctl is-active keepalived"]="active"
        ["slave1:systemctl is-active keepalived"]="active"
        ["slave0:ip -4 addr show eth0 | grep -c 192.168.0.53"]="1"
        ["slave1:ip -4 addr show eth0 | grep -c 192.168.0.53"]="1"
    )

    check_keepalived

    [ "${RESULTS[keepalived]}" = "down" ] && \
        pass "H2: DOWN on split-brain (VIP on both)" || \
        fail "H2: DOWN on split-brain" "got: ${RESULTS[keepalived]:-unset}"
    [[ "${ERRORS[keepalived]:-}" == *"SPLIT-BRAIN"* ]] && \
        pass "H2: error mentions SPLIT-BRAIN" || \
        fail "H2: error mentions SPLIT-BRAIN" "got: ${ERRORS[keepalived]:-unset}"
}

run_h3() {
    _reset
    _TIMED_SSH_RESPONSES=(
        ["slave0:systemctl is-active keepalived"]="active"
        ["slave1:systemctl is-active keepalived"]="active"
        ["slave0:ip -4 addr show eth0 | grep -c 192.168.0.53"]="0"
        ["slave1:ip -4 addr show eth0 | grep -c 192.168.0.53"]="0"
    )

    check_keepalived

    [ "${RESULTS[keepalived]}" = "degraded" ] && \
        pass "H3: DEGRADED when VIP missing from both nodes" || \
        fail "H3: DEGRADED when no VIP" "got: ${RESULTS[keepalived]:-unset}"
}

run_h4() {
    _reset
    _TIMED_SSH_RESPONSES=(
        ["slave0:systemctl is-active keepalived"]="active"
        ["slave1:systemctl is-active keepalived"]="inactive"
        ["slave0:ip -4 addr show eth0 | grep -c 192.168.0.53"]="1"
    )

    check_keepalived

    [ "${RESULTS[keepalived]}" = "degraded" ] && \
        pass "H4: DEGRADED when one node down" || \
        fail "H4: DEGRADED when one down" "got: ${RESULTS[keepalived]:-unset}"
    [[ "${ERRORS[keepalived]:-}" == *"slave1"* ]] && \
        pass "H4: error mentions downed node" || \
        fail "H4: error mentions slave1" "got: ${ERRORS[keepalived]:-unset}"
}

# ═══════════ Check N: NFS Mount Health ═══════════

_NFS_CLIENT_CMD="mountpoint -q /mnt/external && timeout 3 stat -t /mnt/external >/dev/null 2>&1 && echo ok"

run_n1() {
    _reset
    hostname() { echo "heavy"; }
    showmount() { echo "/mnt/data 192.168.0.0/24"; }
    export -f hostname showmount
    _TIMED_SSH_RESPONSES=(
        ["master:${_NFS_CLIENT_CMD}"]="ok"
        ["slave0:${_NFS_CLIENT_CMD}"]="ok"
        ["slave1:${_NFS_CLIENT_CMD}"]="ok"
    )

    check_nfs_mount

    [ "${RESULTS[nfs-server]}" = "up" ] && \
        pass "N1: nfs-server UP when exporting /mnt/data" || \
        fail "N1: nfs-server UP" "got: ${RESULTS[nfs-server]:-unset}"
    [ "${RESULTS[nfs-mount]}" = "up" ] && \
        pass "N1: nfs-mount UP when all clients healthy" || \
        fail "N1: nfs-mount UP" "got: ${RESULTS[nfs-mount]:-unset}"

    unset -f hostname showmount
}

run_n2() {
    _reset
    hostname() { echo "heavy"; }
    showmount() { echo ""; }
    export -f hostname showmount

    check_nfs_mount

    [ "${RESULTS[nfs-server]}" = "down" ] && \
        pass "N2: nfs-server DOWN when not exporting" || \
        fail "N2: nfs-server DOWN" "got: ${RESULTS[nfs-server]:-unset}"

    unset -f hostname showmount
}

run_n3() {
    _reset
    hostname() { echo "master"; }
    mountpoint() { return 0; }
    timeout() { shift; "$@"; }
    stat() { return 0; }
    export -f hostname mountpoint timeout stat

    check_nfs_mount

    [ "${RESULTS[nfs-mount]}" = "up" ] && \
        pass "N3: nfs-mount UP when mounted and responsive" || \
        fail "N3: nfs-mount UP" "got: ${RESULTS[nfs-mount]:-unset}"

    unset -f hostname mountpoint timeout stat
}

run_n4() {
    _reset
    hostname() { echo "master"; }
    mountpoint() { return 1; }
    export -f hostname mountpoint

    check_nfs_mount

    [ "${RESULTS[nfs-mount]}" = "down" ] && \
        pass "N4: nfs-mount DOWN when not mounted" || \
        fail "N4: nfs-mount DOWN" "got: ${RESULTS[nfs-mount]:-unset}"
    [[ "${ERRORS[nfs-mount]:-}" == *"not mounted"* ]] && \
        pass "N4: error says not mounted" || \
        fail "N4: error says not mounted" "got: ${ERRORS[nfs-mount]:-unset}"

    unset -f hostname mountpoint
}

run_n5() {
    _reset
    hostname() { echo "heavy"; }
    showmount() { echo "/mnt/data 192.168.0.0/24"; }
    export -f hostname showmount
    _TIMED_SSH_RESPONSES=(
        ["master:${_NFS_CLIENT_CMD}"]="ok"
        ["slave0:${_NFS_CLIENT_CMD}"]="ok"
        ["slave1:${_NFS_CLIENT_CMD}"]=""
    )

    check_nfs_mount

    [ "${RESULTS[nfs-mount]}" = "degraded" ] && \
        pass "N5: nfs-mount DEGRADED when one client unhealthy" || \
        fail "N5: nfs-mount DEGRADED" "got: ${RESULTS[nfs-mount]:-unset}"
    [[ "${ERRORS[nfs-mount]:-}" == *"slave1"* ]] && \
        pass "N5: error names unhealthy node" || \
        fail "N5: error names slave1" "got: ${ERRORS[nfs-mount]:-unset}"

    unset -f hostname showmount
}

run_n6() {
    _reset
    hostname() { echo "heavy"; }
    showmount() { echo "/mnt/data 192.168.0.0/24"; }
    export -f hostname showmount
    _TIMED_SSH_RESPONSES=()

    check_nfs_mount

    [ "${RESULTS[nfs-mount]}" = "degraded" ] && \
        pass "N6: nfs-mount DEGRADED when all clients unreachable" || \
        fail "N6: nfs-mount DEGRADED" "got: ${RESULTS[nfs-mount]:-unset}"

    unset -f hostname showmount
}

# ═══════════ Check G: Gravity Sync ═══════════

run_g1() {
    _reset
    _GRAVITY_SYNC_DATA="active active 0 300"
    check_gravity_sync
    [ "${RESULTS[gravity-sync]}" = "up" ] && \
        pass "G1: UP when both timers active, exit 0, age 300s" || \
        fail "G1: UP" "got: ${RESULTS[gravity-sync]:-unset}"
}

run_g2() {
    _reset
    _GRAVITY_SYNC_DATA="inactive inactive 0 -1"
    check_gravity_sync
    [ "${RESULTS[gravity-sync]}" = "down" ] && \
        pass "G2: DOWN when both timers inactive" || \
        fail "G2: DOWN" "got: ${RESULTS[gravity-sync]:-unset}"
}

run_g3() {
    _reset
    _GRAVITY_SYNC_DATA="active inactive 0 300"
    check_gravity_sync
    [ "${RESULTS[gravity-sync]}" = "degraded" ] && \
        pass "G3: DEGRADED when one timer down" || \
        fail "G3: DEGRADED" "got: ${RESULTS[gravity-sync]:-unset}"
    [[ "${ERRORS[gravity-sync]:-}" == *"slave1"* ]] && \
        pass "G3: error names downed node" || \
        fail "G3: error names slave1" "got: ${ERRORS[gravity-sync]:-unset}"
}

run_g4() {
    _reset
    _GRAVITY_SYNC_DATA="active active 1 300"
    check_gravity_sync
    [ "${RESULTS[gravity-sync]}" = "degraded" ] && \
        pass "G4: DEGRADED when last run failed (exit 1)" || \
        fail "G4: DEGRADED on failure" "got: ${RESULTS[gravity-sync]:-unset}"
}

run_g5() {
    _reset
    _GRAVITY_SYNC_DATA="active active 0 2400"
    check_gravity_sync
    [ "${RESULTS[gravity-sync]}" = "degraded" ] && \
        pass "G5: DEGRADED when last run >1800s ago" || \
        fail "G5: DEGRADED on stale" "got: ${RESULTS[gravity-sync]:-unset}"
}

run_g6() {
    _reset
    _GRAVITY_SYNC_DATA="active active unknown -1"
    check_gravity_sync
    [ "${RESULTS[gravity-sync]}" = "up" ] && \
        pass "G6: UP when exit unknown and age unknown (never ran but timers active)" || \
        fail "G6: UP" "got: ${RESULTS[gravity-sync]:-unset}"
}

run_h1; run_h2; run_h3; run_h4
run_n1; run_n2; run_n3; run_n4; run_n5; run_n6
run_g1; run_g2; run_g3; run_g4; run_g5; run_g6

test_summary
