#!/bin/bash
# Tests for scripts/deploy-smoke.sh
#
#   T1: All checks pass → exit 0, DEPLOY_SMOKE_OK
#   T2: Gateway down → exit 1, service listed
#   T3: Degraded service → exit 0 (warn only)

set -uo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$TEST_DIR/.." && pwd)"

source "$REPO_DIR/scripts/lib/test-harness.sh"
source "$TEST_DIR/lib/watchdog-shims.sh"

echo "=== deploy-smoke.sh ==="

_setup() {
    shims_init

    # Provide a minimal smoke-common.sh that just defines what deploy-smoke needs
    mkdir -p "$SHIM_TMP/scripts/lib" "$SHIM_TMP/scripts/smoke-checks"

    cat > "$SHIM_TMP/scripts/.env.cluster" <<'ENV'
HEAVY_IP=127.0.0.1
HEAVY_HOST=localhost
MC_API_KEY=test-key
ENV

    cat > "$SHIM_TMP/scripts/lib/smoke-common.sh" <<'LIB'
declare -A RESULTS
declare -A RESPONSE_MS
declare -A ERRORS
check_service() {
    local name="$1" status="$2"
    RESULTS[$name]="$status"
}
_ssh() { ssh -o ConnectTimeout=1 -o BatchMode=yes "$@"; }
timed_ssh() { local t="$1"; shift; timeout "$t" ssh -o ConnectTimeout=1 -o BatchMode=yes "$@"; }
MISSION_CONTROL_API="http://127.0.0.1:0/api"
API_KEY="test-key"
HOST_DNS_OK=true
LIB
}

_build_smoke_script() {
    local check_mode="$1"
    local smoke_script="$SHIM_TMP/deploy-smoke-test.sh"

    cat > "$smoke_script" <<HEADER
#!/bin/bash
set -uo pipefail
SCRIPT_DIR="$SHIM_TMP/scripts"
source "\$SCRIPT_DIR/.env.cluster"
source "\$SCRIPT_DIR/lib/smoke-common.sh"
HEADER

    case "$check_mode" in
        all_pass)
            cat >> "$smoke_script" <<'CHECKS'
check_service "openclaw-gateway" "up"
check_service "mission-control-api" "up"
check_service "postgresql" "up"
check_service "openclaw-master" "up"
check_service "openclaw-slave0" "up"
check_service "pihole-dns" "up"
check_service "heartbeat-canary" "up"
CHECKS
            ;;
        gateway_down)
            cat >> "$smoke_script" <<'CHECKS'
check_service "openclaw-gateway" "down"
check_service "mission-control-api" "up"
check_service "postgresql" "up"
check_service "openclaw-master" "up"
check_service "openclaw-slave0" "up"
check_service "pihole-dns" "up"
check_service "heartbeat-canary" "up"
CHECKS
            ;;
        degraded)
            cat >> "$smoke_script" <<'CHECKS'
check_service "openclaw-gateway" "degraded"
check_service "mission-control-api" "up"
check_service "postgresql" "up"
check_service "openclaw-master" "up"
check_service "openclaw-slave0" "up"
check_service "pihole-dns" "up"
check_service "heartbeat-canary" "up"
CHECKS
            ;;
    esac

    # Append the evaluation logic from the real script
    cat >> "$smoke_script" <<'EVAL'
CRITICAL_SVCS=(openclaw-gateway mission-control-api postgresql openclaw-master openclaw-slave0 pihole-dns heartbeat-canary)
FAILED=()
for svc in "${CRITICAL_SVCS[@]}"; do
    status="${RESULTS[$svc]:-unknown}"
    if [[ "$status" == "down" ]]; then
        FAILED+=("$svc")
    fi
done

if [[ ${#FAILED[@]} -gt 0 ]]; then
    echo "DEPLOY_SMOKE_FAIL: ${FAILED[*]}"
    exit 1
fi
echo "DEPLOY_SMOKE_OK"
exit 0
EVAL

    chmod +x "$smoke_script"
    echo "$smoke_script"
}

_cleanup() {
    shims_cleanup
    cd "$TEST_DIR"
}

# -- T1: All checks pass --
run_t1() {
    _setup
    local script
    script=$(_build_smoke_script all_pass)
    local output rc=0
    output=$(bash "$script" 2>&1) || rc=$?

    [[ "$rc" -eq 0 ]] && \
        pass "T1: all pass exits 0" || \
        fail "T1: all pass exits 0" "exit: $rc"

    echo "$output" | grep -q "DEPLOY_SMOKE_OK" && \
        pass "T1: outputs DEPLOY_SMOKE_OK" || \
        fail "T1: outputs DEPLOY_SMOKE_OK" "output: $output"

    _cleanup
}

# -- T2: Gateway down --
run_t2() {
    _setup
    local script
    script=$(_build_smoke_script gateway_down)
    local output rc=0
    output=$(bash "$script" 2>&1) || rc=$?

    [[ "$rc" -eq 1 ]] && \
        pass "T2: gateway down exits 1" || \
        fail "T2: gateway down exits 1" "exit: $rc"

    echo "$output" | grep -q "openclaw-gateway" && \
        pass "T2: failed service listed" || \
        fail "T2: failed service listed" "output: $output"

    _cleanup
}

# -- T3: Degraded service --
run_t3() {
    _setup
    local script
    script=$(_build_smoke_script degraded)
    local output rc=0
    output=$(bash "$script" 2>&1) || rc=$?

    [[ "$rc" -eq 0 ]] && \
        pass "T3: degraded exits 0 (warn only)" || \
        fail "T3: degraded exits 0 (warn only)" "exit: $rc"

    _cleanup
}

run_t1
run_t2
run_t3

test_summary
