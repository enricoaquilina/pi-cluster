#!/bin/bash
# Test the shared test harness library
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$SCRIPT_DIR/scripts/lib/test-harness.sh"

ERRORS=0

# Test pass()
pass "test-pass"
[ "$PASS" -eq 1 ] || { echo "FAIL: PASS counter should be 1, got $PASS"; ERRORS=$((ERRORS+1)); }

# Test fail()
fail "test-fail" "reason" > /dev/null
[ "$FAIL" -eq 1 ] || { echo "FAIL: FAIL counter should be 1, got $FAIL"; ERRORS=$((ERRORS+1)); }

# Test warn()
warn "test-warn" "info" > /dev/null
[ "$WARN" -eq 1 ] || { echo "FAIL: WARN counter should be 1, got $WARN"; ERRORS=$((ERRORS+1)); }

# Test TESTS array
[ "${#TESTS[@]}" -eq 3 ] || { echo "FAIL: TESTS array should have 3 entries, got ${#TESTS[@]}"; ERRORS=$((ERRORS+1)); }
echo "${TESTS[0]}" | grep -q "^PASS" || { echo "FAIL: First entry should be PASS"; ERRORS=$((ERRORS+1)); }
echo "${TESTS[1]}" | grep -q "^FAIL" || { echo "FAIL: Second entry should be FAIL"; ERRORS=$((ERRORS+1)); }
echo "${TESTS[2]}" | grep -q "^WARN" || { echo "FAIL: Third entry should be WARN"; ERRORS=$((ERRORS+1)); }

# Test fix-hints (should show hint when enabled)
_HARNESS_FIX_HINTS=true
hint_output=$(fail "test-hint" "error" "run fix-command" 2>&1)
echo "$hint_output" | grep -q "Fix: run fix-command" || { echo "FAIL: Fix hint not shown"; ERRORS=$((ERRORS+1)); }
_HARNESS_FIX_HINTS=false

# Test CI mode suppresses pass output
_HARNESS_CI=true
ci_output=$(pass "silent-pass" 2>&1)
[ -z "$ci_output" ] || { echo "FAIL: CI mode should suppress pass output"; ERRORS=$((ERRORS+1)); }
_HARNESS_CI=false

# Test summary
summary_output=$(test_summary 2>&1) || true  # Will return non-zero due to FAIL count
echo "$summary_output" | grep -q "Failures:" || { echo "FAIL: Summary should list failures"; ERRORS=$((ERRORS+1)); }

echo ""
if [ "$ERRORS" -eq 0 ]; then
    echo "=== Test harness: ALL TESTS PASSED ==="
else
    echo "=== Test harness: $ERRORS FAILURES ==="
    exit 1
fi
