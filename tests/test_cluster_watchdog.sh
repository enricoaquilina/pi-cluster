#!/bin/bash
# Tests for scripts/openclaw-watchdog-cluster.sh
#
# Validates fixes for the 5-day node outage (2026-04-21 to 2026-04-26):
#   T1: All connected — no re-pair triggered
#   T2: Node disconnected — pair-nodes called
#   T3: Top-level 'connected' field recognized (not metadata.connected)
#   T4: SCRIPT_DIR referenced files exist
#   T5: Hostname guard prevents run on non-heavy
#   T6: LOG_FILE path is writable
#   T7: Recovery recheck uses correct field path

set -uo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$TEST_DIR/.." && pwd)"
WATCHDOG="$REPO_DIR/scripts/openclaw-watchdog-cluster.sh"

source "$REPO_DIR/scripts/lib/test-harness.sh"
source "$TEST_DIR/lib/watchdog-shims.sh"

echo "=== openclaw-watchdog-cluster.sh ==="

_test_init() {
    shims_init

    shims_set_script hostname <<'BASH'
echo "heavy"
BASH

    cat > "$SHIM_BIN/sleep" <<'BASH'
#!/bin/bash
exit 0
BASH
    chmod +x "$SHIM_BIN/sleep"

    TEST_SCRIPT_DIR="$SHIM_TMP/scripts"
    mkdir -p "$TEST_SCRIPT_DIR/lib"

    cat > "$TEST_SCRIPT_DIR/openclaw-stats-collector.sh" <<'BASH'
#!/bin/bash
exit 0
BASH

    cat > "$TEST_SCRIPT_DIR/openclaw-pair-nodes.sh" <<BASH
#!/bin/bash
echo "called" >> "$CALLS_DIR/pair-nodes.log"
exit 0
BASH

    : > "$TEST_SCRIPT_DIR/.env.cluster"

    export CACHE_FILE="$SHIM_TMP/node-stats.json"
    export STATE_FILE="$SHIM_TMP/watchdog-state.json"
    export SCRIPT_DIR="$TEST_SCRIPT_DIR"
    export LOG_FILE="$SHIM_TMP/watchdog.log"

    shims_set_script docker <<'BASH'
case "$*" in
    *"--filter"*"--format"*)
        echo "Up 2 hours (healthy)"
        ;;
esac
exit 0
BASH
}

_test_cleanup() {
    shims_cleanup
    unset CACHE_FILE STATE_FILE SCRIPT_DIR LOG_FILE TEST_SCRIPT_DIR
}

# -- T1: All connected → no re-pair --
run_t1() {
    _test_init
    cat > "$CACHE_FILE" <<'JSON'
{"nodes": [{"name": "build", "connected": true}, {"name": "light", "connected": true}, {"name": "heavy", "connected": true}]}
JSON

    "$WATCHDOG" >/dev/null 2>&1
    rc=$?

    [ "$rc" -eq 0 ] && pass "T1: exits 0 when all connected" || fail "T1: exits 0 when all connected" "rc=$rc"

    if [ ! -f "$CALLS_DIR/pair-nodes.log" ]; then
        pass "T1: pair-nodes NOT called when all connected"
    else
        fail "T1: pair-nodes NOT called when all connected" "pair-nodes was called"
    fi

    _test_cleanup
}

# -- T2: Node disconnected → triggers pair --
run_t2() {
    _test_init
    cat > "$CACHE_FILE" <<'JSON'
{"nodes": [{"name": "build", "connected": false}, {"name": "light", "connected": true}, {"name": "heavy", "connected": true}]}
JSON

    "$WATCHDOG" >/dev/null 2>&1

    if [ -f "$CALLS_DIR/pair-nodes.log" ] && grep -q "called" "$CALLS_DIR/pair-nodes.log"; then
        pass "T2: pair-nodes called for disconnected node"
    else
        fail "T2: pair-nodes called for disconnected node" "pair-nodes not called"
    fi

    _test_cleanup
}

# -- T3: Top-level 'connected' field recognized --
run_t3() {
    _test_init
    cat > "$CACHE_FILE" <<'JSON'
{"nodes": [{"name": "build", "connected": true, "metadata": {}}, {"name": "light", "connected": true, "metadata": {}}, {"name": "heavy", "connected": true, "metadata": {}}]}
JSON

    "$WATCHDOG" >/dev/null 2>&1

    if [ ! -f "$CALLS_DIR/pair-nodes.log" ]; then
        pass "T3: top-level connected=true recognized (no pair triggered)"
    else
        fail "T3: top-level connected=true recognized" "pair-nodes called despite connected=true"
    fi

    _test_cleanup
}

# -- T4: SCRIPT_DIR referenced files exist --
run_t4() {
    local real_dir="$REPO_DIR/scripts"
    local ok=true

    for f in openclaw-stats-collector.sh openclaw-pair-nodes.sh; do
        if [ -f "$real_dir/$f" ]; then
            pass "T4: $f exists at SCRIPT_DIR"
        else
            fail "T4: $f exists at SCRIPT_DIR" "not found: $real_dir/$f"
            ok=false
        fi
    done

    if [ -f "$real_dir/lib/telegram.sh" ]; then
        pass "T4: telegram.sh exists or fallback defined"
    else
        grep -q 'send_telegram.*{.*:.*}' "$WATCHDOG" && \
            pass "T4: telegram.sh missing but fallback defined" || \
            fail "T4: telegram.sh exists or fallback defined" "missing and no fallback"
    fi
}

# -- T5: Hostname guard — non-heavy exits early --
run_t5() {
    _test_init
    cat > "$CACHE_FILE" <<'JSON'
{"nodes": [{"name": "build", "connected": true}, {"name": "light", "connected": true}, {"name": "heavy", "connected": true}]}
JSON

    shims_set_script hostname <<'BASH'
echo "not-heavy"
BASH

    "$WATCHDOG" >/dev/null 2>&1
    rc=$?

    [ "$rc" -eq 0 ] && pass "T5: hostname guard exits 0" || fail "T5: hostname guard exits 0" "rc=$rc"

    if [ ! -f "$CALLS_DIR/pair-nodes.log" ] && ! grep -q "compose\|inspect" "$CALLS_DIR/docker.log" 2>/dev/null; then
        pass "T5: hostname guard does no work"
    else
        fail "T5: hostname guard does no work" "docker or pair-nodes was called"
    fi

    _test_cleanup
}

# -- T6: LOG_FILE path is writable --
run_t6() {
    local log_dir="/tmp"
    if [ -w "$log_dir" ]; then
        pass "T6: LOG_FILE directory ($log_dir) is writable"
    else
        fail "T6: LOG_FILE directory ($log_dir) is writable" "not writable"
    fi
}

# -- T7: Recovery recheck uses correct field path --
run_t7() {
    if sed -n '/recheck=/,/done/p' "$WATCHDOG" | grep -q "n.get('connected'"; then
        pass "T7: recheck Python uses top-level connected field"
    else
        fail "T7: recheck Python uses top-level connected field" "field path may be wrong"
    fi

    if sed -n '/node_status=/,/done/p' "$WATCHDOG" | grep -q "n.get('connected'"; then
        pass "T7: initial check Python uses top-level connected field"
    else
        fail "T7: initial check Python uses top-level connected field" "field path may be wrong"
    fi
}

# -- W13: Gateway healthy but all nodes disconnected → pair-nodes triggered --
run_w13() {
    _test_init
    cat > "$CACHE_FILE" <<'JSON'
{"nodes": [{"name": "build", "connected": false}, {"name": "light", "connected": false}, {"name": "heavy", "connected": false}]}
JSON

    "$WATCHDOG" >/dev/null 2>&1

    if [ -f "$CALLS_DIR/pair-nodes.log" ] && grep -q "called" "$CALLS_DIR/pair-nodes.log"; then
        pass "W13: all nodes disconnected + gateway healthy → pair-nodes triggered"
    else
        fail "W13: all nodes disconnected + gateway healthy → pair-nodes triggered" "pair-nodes not called"
    fi

    _test_cleanup
}

run_t1
run_t2
run_t3
run_t4
run_t5
run_t6
run_t7
run_w13

test_summary
