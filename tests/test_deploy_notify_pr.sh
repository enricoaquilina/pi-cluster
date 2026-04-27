#!/bin/bash
# Tests for scripts/lib/deploy-notify-pr.sh
#
#   N1: Extract PR number from squash commit message
#   N2: No PR number in message → no-op (no curl call)
#   N3: Missing GITHUB_PAT → no-op (no curl call)

set -uo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$TEST_DIR/.." && pwd)"

source "$REPO_DIR/scripts/lib/test-harness.sh"
source "$TEST_DIR/lib/watchdog-shims.sh"

echo "=== deploy-notify-pr.sh ==="

_setup() {
    shims_init
    shims_set_script jq <<'BASH'
if [[ "$*" == *"-Rs"* ]]; then
    input=$(cat)
    echo "\"$input\""
else
    echo "$@"
fi
exit 0
BASH
    _shim_default jq 2>/dev/null || true
    source "$REPO_DIR/scripts/lib/deploy-notify-pr.sh"
}

_cleanup() {
    shims_cleanup
    cd "$TEST_DIR"
}

# -- N1: Extract PR number from squash commit message --
run_n1() {
    _setup
    export GITHUB_PAT="test-token"

    deploy_notify_pr "fix: something important (#123)" "test body"

    local curl_log
    curl_log=$(shims_calls curl)
    if echo "$curl_log" | grep -q "issues/123/comments"; then
        pass "N1: extracts PR number from commit message"
    else
        fail "N1: extracts PR number from commit message" "curl log: $curl_log"
    fi

    _cleanup
}

# -- N2: No PR number → no curl call --
run_n2() {
    _setup
    export GITHUB_PAT="test-token"

    deploy_notify_pr "random commit message" "test body"

    local call_count
    call_count=$(shims_call_count curl "issues")
    if [[ "$call_count" -eq 0 ]]; then
        pass "N2: no PR number → no curl call"
    else
        fail "N2: no PR number → no curl call" "curl called $call_count times"
    fi

    _cleanup
}

# -- N3: Missing GITHUB_PAT → no curl call --
run_n3() {
    _setup
    unset GITHUB_PAT

    deploy_notify_pr "fix: something (#456)" "test body"

    local call_count
    call_count=$(shims_call_count curl "issues")
    if [[ "$call_count" -eq 0 ]]; then
        pass "N3: missing GITHUB_PAT → no curl call"
    else
        fail "N3: missing GITHUB_PAT → no curl call" "curl called $call_count times"
    fi

    _cleanup
}

run_n1
run_n2
run_n3

test_summary
