#!/bin/bash
# Tests for scripts/lib/deploy-rollback.sh
#
#   R1: deploy_mark_stable creates/updates the tag
#   R2: deploy_can_rollback false when HEAD == tag
#   R3: deploy_can_rollback true when HEAD != tag
#   R4: deploy_rollback resets HEAD to tag

set -uo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$TEST_DIR/.." && pwd)"

source "$REPO_DIR/scripts/lib/test-harness.sh"
source "$TEST_DIR/lib/watchdog-shims.sh"

echo "=== deploy-rollback.sh ==="

_setup() {
    shims_init

    WORK_REPO="$SHIM_TMP/work"
    BARE_REMOTE="$SHIM_TMP/remote.git"

    git init --bare "$BARE_REMOTE" >/dev/null 2>&1
    git clone "$BARE_REMOTE" "$WORK_REPO" >/dev/null 2>&1

    cd "$WORK_REPO"
    git config user.email "test@test.com"
    git config user.name "test"
    mkdir -p playbooks
    echo "init" > README.md
    : > playbooks/openclaw-monitoring.yml
    : > playbooks/monitoring.yml
    git add -A && git commit -m "initial" >/dev/null 2>&1
    git push origin master >/dev/null 2>&1

    export REPO_DIR="$WORK_REPO"

    log() { :; }
    export -f log

    shims_set_script ansible-playbook <<'BASH'
exit 0
BASH

    shims_set_script ssh <<'BASH'
exit 0
BASH

    source "$TEST_DIR/../scripts/lib/deploy-rollback.sh"
}

_cleanup() {
    shims_cleanup
    cd "$TEST_DIR"
}

# -- R1: deploy_mark_stable creates tag --
run_r1() {
    _setup

    deploy_mark_stable

    git rev-parse "deploy/stable" >/dev/null 2>&1 && \
        pass "R1: deploy_mark_stable creates tag" || \
        fail "R1: deploy_mark_stable creates tag" "tag not found"

    local tag_ref head_ref
    tag_ref=$(git rev-parse "deploy/stable")
    head_ref=$(git rev-parse HEAD)
    [[ "$tag_ref" == "$head_ref" ]] && \
        pass "R1: tag points to HEAD" || \
        fail "R1: tag points to HEAD" "tag=$tag_ref HEAD=$head_ref"

    _cleanup
}

# -- R2: deploy_can_rollback false when HEAD == tag --
run_r2() {
    _setup

    deploy_mark_stable

    deploy_can_rollback
    local rc=$?

    [[ "$rc" -ne 0 ]] && \
        pass "R2: can_rollback false when HEAD == tag" || \
        fail "R2: can_rollback false when HEAD == tag" "returned 0 (true)"

    _cleanup
}

# -- R3: deploy_can_rollback true when HEAD != tag --
run_r3() {
    _setup

    deploy_mark_stable

    echo "new change" > newfile.txt
    git add newfile.txt && git commit -m "new commit" >/dev/null 2>&1

    deploy_can_rollback
    local rc=$?

    [[ "$rc" -eq 0 ]] && \
        pass "R3: can_rollback true when HEAD != tag" || \
        fail "R3: can_rollback true when HEAD != tag" "returned $rc"

    _cleanup
}

# -- R4: deploy_rollback resets HEAD to tag --
run_r4() {
    _setup

    local stable_sha
    stable_sha=$(git rev-parse HEAD)
    deploy_mark_stable

    echo "bad change" > badfile.txt
    git add badfile.txt && git commit -m "bad commit" >/dev/null 2>&1

    local bad_sha
    bad_sha=$(git rev-parse HEAD)
    [[ "$bad_sha" != "$stable_sha" ]] && \
        pass "R4: HEAD moved past stable" || \
        fail "R4: HEAD moved past stable" "same SHA"

    deploy_rollback

    local post_rollback
    post_rollback=$(git rev-parse HEAD)
    [[ "$post_rollback" == "$stable_sha" ]] && \
        pass "R4: rollback resets HEAD to stable" || \
        fail "R4: rollback resets HEAD to stable" "HEAD=$post_rollback expected=$stable_sha"

    _cleanup
}

run_r1
run_r2
run_r3
run_r4

test_summary
