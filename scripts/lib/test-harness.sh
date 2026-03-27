#!/bin/bash
# Shared test harness — source from test scripts.
# Provides: pass(), fail(), warn(), test_summary()
# Supports: TESTS array, CI mode, fix-hints, Telegram notifications.
#
# Usage:
#   source scripts/lib/test-harness.sh
#   pass "Check passed"
#   fail "Check failed" "reason"
#   fail "Check failed" "reason" "fix command"  # with --fix-hints
#   warn "Check warned" "info"
#   test_summary  # prints results, exits 0 if no failures

PASS=0
FAIL=0
WARN=0
TESTS=()

# Override these before sourcing or via flags
_HARNESS_CI=${_HARNESS_CI:-false}
_HARNESS_FIX_HINTS=${_HARNESS_FIX_HINTS:-false}

pass() {
    PASS=$((PASS + 1))
    TESTS+=("PASS  $1")
    $_HARNESS_CI || echo "  PASS  $1"
}

fail() {
    FAIL=$((FAIL + 1))
    TESTS+=("FAIL  $1: $2")
    echo "  FAIL  $1: $2"
    $_HARNESS_FIX_HINTS && [ -n "${3:-}" ] && echo "        Fix: $3"
}

warn() {
    WARN=$((WARN + 1))
    TESTS+=("WARN  $1: $2")
    $_HARNESS_CI || echo "  WARN  $1: $2"
}

test_summary() {
    echo ""
    echo "=== Results ==="
    echo "Passed: $PASS"
    [ "$WARN" -gt 0 ] && echo "Warned: $WARN"
    echo "Failed: $FAIL"
    echo "Total:  $((PASS + FAIL + WARN))"
    if [ "$FAIL" -gt 0 ]; then
        echo ""
        echo "Failures:"
        for t in "${TESTS[@]}"; do
            echo "$t" | grep "^FAIL" || true
        done
    fi
    [ "$FAIL" -eq 0 ]
}
