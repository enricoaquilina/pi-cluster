#!/bin/bash
# Tests for quick-win smoke checks:
#   13-node-staleness.sh — push_ts freshness detection
#   14-watchdogs.sh — watchdog timer health
#   10-storage.sh — NFS ownership guard (enhanced)
#   15-orphan-services.sh — orphan service detection
#
# 16 test cases covering all status combinations.

set -uo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$TEST_DIR/.." && pwd)"

source "$REPO_DIR/scripts/lib/test-harness.sh"

echo "=== Quick-Win Smoke Checks ==="

HEAVY_IP="127.0.0.1"
declare -A RESULTS RESPONSE_MS ERRORS

check_service() {
    local name="$1" status="$2" error="${3:-}" ms="${4:-}"
    RESULTS[$name]="$status"
    [[ -n "$error" ]] && ERRORS[$name]="$error"
    [[ -n "$ms" ]] && RESPONSE_MS[$name]="$ms"
}

timed_ssh() { :; }

source "$REPO_DIR/scripts/smoke-checks/10-storage.sh"
source "$REPO_DIR/scripts/smoke-checks/13-node-staleness.sh"
source "$REPO_DIR/scripts/smoke-checks/14-watchdogs.sh"
source "$REPO_DIR/scripts/smoke-checks/15-orphan-services.sh"

_reset() {
    RESULTS=()
    RESPONSE_MS=()
    ERRORS=()
    _NODE_STALENESS_DATA=""
    _WATCHDOG_STATUS=""
    _NFS_OWNERSHIP_DATA=""
    _ORPHAN_SERVICE_DATA=""
}

# ═══════════ Check A: Node Stats Staleness ═══════════

run_a1() {
    _reset
    _NODE_STALENESS_DATA="heavy 1777218830.59 45
control 1777218829.00 46
build 1777218828.00 47
light 1777218827.00 48"

    check_node_stats_heavy
    check_node_stats_control

    [ "${RESULTS[node-stats-heavy]}" = "up" ] && \
        pass "A1: heavy UP when stats fresh (45s)" || \
        fail "A1: heavy UP when stats fresh" "got: ${RESULTS[node-stats-heavy]:-unset}"
    [ "${RESULTS[node-stats-control]}" = "up" ] && \
        pass "A1: control UP when stats fresh (46s)" || \
        fail "A1: control UP when stats fresh" "got: ${RESULTS[node-stats-control]:-unset}"
}

run_a2() {
    _reset
    _NODE_STALENESS_DATA="heavy 1777218830.59 45
control 1777218830.59 45
build 1777218830.59 600
light 1777218830.59 45"

    check_node_stats_build
    check_node_stats_light

    [ "${RESULTS[node-stats-build]}" = "down" ] && \
        pass "A2: build DOWN when stale (600s)" || \
        fail "A2: build DOWN when stale" "got: ${RESULTS[node-stats-build]:-unset}"
    [ "${RESULTS[node-stats-light]}" = "up" ] && \
        pass "A2: light UP when fresh" || \
        fail "A2: light UP when fresh" "got: ${RESULTS[node-stats-light]:-unset}"
}

run_a3() {
    _reset
    _NODE_STALENESS_DATA="heavy 0 -1
control 1777218830.59 45
build 1777218830.59 45
light 1777218830.59 45"

    check_node_stats_heavy

    [ "${RESULTS[node-stats-heavy]}" = "down" ] && \
        pass "A3: heavy DOWN when push_ts=0 (never pushed)" || \
        fail "A3: heavy DOWN when never pushed" "got: ${RESULTS[node-stats-heavy]:-unset}"
}

run_a4() {
    _reset
    _NODE_STALENESS_DATA="api_unreachable"

    check_node_stats_heavy
    check_node_stats_control

    [ "${RESULTS[node-stats-heavy]}" = "down" ] && \
        pass "A4: heavy DOWN when API unreachable" || \
        fail "A4: heavy DOWN when API unreachable" "got: ${RESULTS[node-stats-heavy]:-unset}"
    [ "${RESULTS[node-stats-control]}" = "down" ] && \
        pass "A4: control DOWN when API unreachable" || \
        fail "A4: control DOWN when API unreachable" "got: ${RESULTS[node-stats-control]:-unset}"
}

run_a5() {
    _reset
    _NODE_STALENESS_DATA="heavy 1777218830.59 45
control 1777218830.59 45
build 1777218830.59 45"

    check_node_stats_light

    [ "${RESULTS[node-stats-light]}" = "down" ] && \
        pass "A5: light DOWN when missing from API response" || \
        fail "A5: light DOWN when missing" "got: ${RESULTS[node-stats-light]:-unset}"
}

# ═══════════ Check B: Watchdog Self-Monitoring ═══════════

run_b1() {
    _reset
    _WATCHDOG_STATUS="openclaw-watchdog-cluster active 60
ssh-watchdog active 55"

    check_watchdog_cluster
    check_watchdog_ssh

    [ "${RESULTS[watchdog-cluster]}" = "up" ] && \
        pass "B1: cluster watchdog UP when active + recent" || \
        fail "B1: cluster watchdog UP" "got: ${RESULTS[watchdog-cluster]:-unset}"
    [ "${RESULTS[watchdog-ssh]}" = "up" ] && \
        pass "B1: ssh watchdog UP when active + recent" || \
        fail "B1: ssh watchdog UP" "got: ${RESULTS[watchdog-ssh]:-unset}"
}

run_b2() {
    _reset
    _WATCHDOG_STATUS="openclaw-watchdog-cluster inactive 0
ssh-watchdog active 55"

    check_watchdog_cluster

    [ "${RESULTS[watchdog-cluster]}" = "down" ] && \
        pass "B2: cluster watchdog DOWN when timer inactive" || \
        fail "B2: cluster watchdog DOWN when inactive" "got: ${RESULTS[watchdog-cluster]:-unset}"
}

run_b3() {
    _reset
    _WATCHDOG_STATUS="openclaw-watchdog-cluster active -1
ssh-watchdog active 55"

    check_watchdog_cluster

    [ "${RESULTS[watchdog-cluster]}" = "degraded" ] && \
        pass "B3: cluster watchdog DEGRADED when never ran" || \
        fail "B3: cluster watchdog DEGRADED when never ran" "got: ${RESULTS[watchdog-cluster]:-unset}"
}

run_b4() {
    _reset
    _WATCHDOG_STATUS="openclaw-watchdog-cluster active 600
ssh-watchdog active 55"

    check_watchdog_cluster

    [ "${RESULTS[watchdog-cluster]}" = "down" ] && \
        pass "B4: cluster watchdog DOWN when last run 600s ago" || \
        fail "B4: cluster watchdog DOWN when stale" "got: ${RESULTS[watchdog-cluster]:-unset}"
}

# ═══════════ Check C: NFS Ownership ═══════════

run_c1() {
    _reset
    _NFS_OWNERSHIP_DATA="clean"
    check_nfs_workspace

    [ "${RESULTS[nfs-workspace]}" = "up" ] && \
        pass "C1: workspace UP when no root files" || \
        fail "C1: workspace UP when no root files" "got: ${RESULTS[nfs-workspace]:-unset}"
}

run_c2() {
    _reset
    _NFS_OWNERSHIP_DATA="1777218830 /mnt/data/openclaw/workspace/proj/config.json
1777218800 /mnt/data/openclaw/workspace/proj/data.db
1777218700 /mnt/data/openclaw/workspace/proj/out/log.txt"
    check_nfs_workspace

    [ "${RESULTS[nfs-workspace]}" = "degraded" ] && \
        pass "C2: workspace DEGRADED with 3 root files" || \
        fail "C2: workspace DEGRADED with root files" "got: ${RESULTS[nfs-workspace]:-unset}"
}

run_c3() {
    _reset
    local data=""
    for i in $(seq 1 15); do
        data+="1777218830 /mnt/data/openclaw/workspace/proj/file${i}.txt"$'\n'
    done
    _NFS_OWNERSHIP_DATA="${data%$'\n'}"
    check_nfs_workspace

    [ "${RESULTS[nfs-workspace]}" = "down" ] && \
        pass "C3: workspace DOWN with 15 root files" || \
        fail "C3: workspace DOWN with many root files" "got: ${RESULTS[nfs-workspace]:-unset}"
}

# ═══════════ Check D: Orphan Services ═══════════

run_d1() {
    _reset
    _ORPHAN_SERVICE_DATA="clean"

    check_orphan_services

    [ "${RESULTS[orphan-services]}" = "up" ] && \
        pass "D1: UP when no orphans found" || \
        fail "D1: UP when no orphans" "got: ${RESULTS[orphan-services]:-unset}"
}

run_d2() {
    _reset
    _ORPHAN_SERVICE_DATA="master running polymarket-bot.service"

    check_orphan_services

    [ "${RESULTS[orphan-services]}" = "down" ] && \
        pass "D2: DOWN when polybot running on master" || \
        fail "D2: DOWN when polybot on master" "got: ${RESULTS[orphan-services]:-unset}"
    echo "${ERRORS[orphan-services]:-}" | grep -q "master" && \
        pass "D2: error mentions master" || \
        fail "D2: error mentions master" "got: ${ERRORS[orphan-services]:-unset}"
}

run_d3() {
    _reset
    _ORPHAN_SERVICE_DATA="master ssh_unreachable -
slave0 ssh_unreachable -"

    check_orphan_services

    [ "${RESULTS[orphan-services]}" = "degraded" ] && \
        pass "D3: DEGRADED when SSH unreachable" || \
        fail "D3: DEGRADED when SSH unreachable" "got: ${RESULTS[orphan-services]:-unset}"
}

run_d4() {
    _reset
    _ORPHAN_SERVICE_DATA="master running polymarket-bot.service
slave0 running spreadbot.service"

    check_orphan_services

    [ "${RESULTS[orphan-services]}" = "down" ] && \
        pass "D4: DOWN with multiple orphans" || \
        fail "D4: DOWN with multiple orphans" "got: ${RESULTS[orphan-services]:-unset}"
}

run_a1; run_a2; run_a3; run_a4; run_a5
run_b1; run_b2; run_b3; run_b4
run_c1; run_c2; run_c3
run_d1; run_d2; run_d3; run_d4

test_summary
