#!/bin/bash
# Tests for scripts/lib/env-validate.sh
#
#   E1: All required vars present → exits 0
#   E2: Missing required var → exits 1 with var name in error
#   E3: warn_env with missing var → warns to stderr, does NOT exit
#   E4: Multiple missing → all names listed in error

set -uo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$TEST_DIR/.." && pwd)"

source "$REPO_DIR/scripts/lib/test-harness.sh"

echo "=== env-validate.sh ==="

# -- E1: All required vars present → exits 0 --
run_e1() {
    local output rc
    output=$(FOO=bar BAZ=qux bash -c "
        source '$REPO_DIR/scripts/lib/env-validate.sh'
        require_env FOO BAZ
        echo OK
    " 2>&1)
    rc=$?

    [ "$rc" -eq 0 ] && [[ "$output" == *"OK"* ]] && \
        pass "E1: all vars present → exits 0" || \
        fail "E1: all vars present → exits 0" "rc=$rc output=$output"
}

# -- E2: Missing required var → exits 1 with var name --
run_e2() {
    local output rc
    output=$(FOO=bar bash -c "
        source '$REPO_DIR/scripts/lib/env-validate.sh'
        require_env FOO MISSING_VAR
        echo SHOULD_NOT_REACH
    " 2>&1)
    rc=$?

    [ "$rc" -eq 1 ] && \
        pass "E2: missing var → exits 1" || \
        fail "E2: missing var → exits 1" "rc=$rc"

    [[ "$output" == *"MISSING_VAR"* ]] && \
        pass "E2: error names the missing var" || \
        fail "E2: error names the missing var" "output=$output"

    [[ "$output" != *"SHOULD_NOT_REACH"* ]] && \
        pass "E2: script did not continue past require_env" || \
        fail "E2: script continued past require_env"
}

# -- E3: warn_env with missing var → warns but does not exit --
run_e3() {
    local output rc
    output=$(bash -c "
        source '$REPO_DIR/scripts/lib/env-validate.sh'
        warn_env MISSING_WARN_VAR
        echo CONTINUED
    " 2>&1)
    rc=$?

    [ "$rc" -eq 0 ] && [[ "$output" == *"CONTINUED"* ]] && \
        pass "E3: warn_env does not exit" || \
        fail "E3: warn_env does not exit" "rc=$rc output=$output"

    [[ "$output" == *"MISSING_WARN_VAR"* ]] && \
        pass "E3: warning names the missing var" || \
        fail "E3: warning names the missing var" "output=$output"
}

# -- E4: Multiple missing → all names listed --
run_e4() {
    local output rc
    output=$(bash -c "
        source '$REPO_DIR/scripts/lib/env-validate.sh'
        require_env ALPHA BETA GAMMA
    " 2>&1)
    rc=$?

    [ "$rc" -eq 1 ] && \
        pass "E4: multiple missing → exits 1" || \
        fail "E4: multiple missing → exits 1" "rc=$rc"

    [[ "$output" == *"ALPHA"* ]] && [[ "$output" == *"BETA"* ]] && [[ "$output" == *"GAMMA"* ]] && \
        pass "E4: all missing var names listed" || \
        fail "E4: all missing var names listed" "output=$output"
}

run_e1
run_e2
run_e3
run_e4

test_summary
