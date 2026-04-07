#!/bin/bash
# Tests for life-git-sync.sh library functions.
# Uses temporary git repos to test sync behavior in isolation.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$SCRIPT_DIR/../lib"
TMPDIR_BASE=""
PASS=0
FAIL=0

pass() { echo "PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL: $1"; FAIL=$((FAIL + 1)); }

cleanup() {
    [ -n "$TMPDIR_BASE" ] && rm -rf "$TMPDIR_BASE"
}
trap cleanup EXIT

setup_test_repos() {
    # Create a bare "remote" and a working "local" clone
    TMPDIR_BASE=$(mktemp -d)
    REMOTE="$TMPDIR_BASE/remote.git"
    LOCAL="$TMPDIR_BASE/local"

    git init --bare --initial-branch=main "$REMOTE" >/dev/null 2>&1
    git clone "$REMOTE" "$LOCAL" >/dev/null 2>&1
    cd "$LOCAL"
    git config user.email "test@test.com"
    git config user.name "Test"
    echo "# test" > README.md
    git add README.md
    git commit -m "initial" >/dev/null 2>&1
    git push origin main >/dev/null 2>&1
}

# Source the library (stub out telegram)
send_telegram() { :; }
export -f send_telegram
source "$LIB_DIR/life-git-sync.sh" 2>/dev/null || {
    echo "FAIL: Cannot source life-git-sync.sh from $LIB_DIR"
    exit 1
}

# --- Test: sync skips non-git directory ---
test_sync_skips_non_git_dir() {
    local tmpdir
    tmpdir=$(mktemp -d)
    life_git_sync "$tmpdir" >/dev/null 2>&1
    local rc=$?
    rm -rf "$tmpdir"
    [ "$rc" -eq 0 ] && pass "sync skips non-git dir" || fail "sync skips non-git dir (exit $rc)"
}

# --- Test: sync commits markdown changes ---
test_sync_commits_md_changes() {
    setup_test_repos
    echo "new content" >> "$LOCAL/note.md"
    life_git_sync "$LOCAL" >/dev/null 2>&1
    local last_msg
    last_msg=$(git -C "$LOCAL" log -1 --format=%s)
    [[ "$last_msg" == auto:* ]] && pass "sync commits md changes" || fail "sync commits md changes (msg: $last_msg)"
}

# --- Test: sync is noop with no changes ---
test_sync_noop_no_changes() {
    setup_test_repos
    local before after
    before=$(git -C "$LOCAL" rev-parse HEAD)
    life_git_sync "$LOCAL" >/dev/null 2>&1
    after=$(git -C "$LOCAL" rev-parse HEAD)
    [ "$before" = "$after" ] && pass "sync noop on no changes" || fail "sync noop on no changes"
}

# --- Test: commit message includes hostname ---
test_sync_commit_includes_hostname() {
    setup_test_repos
    echo "data" >> "$LOCAL/test.md"
    life_git_sync "$LOCAL" >/dev/null 2>&1
    local msg
    msg=$(git -C "$LOCAL" log -1 --format=%s)
    local expected_host
    expected_host=$(hostname)
    [[ "$msg" == *"$expected_host"* ]] && pass "commit includes hostname" || fail "commit includes hostname (msg: $msg)"
}

# --- Test: pull handles no remote gracefully ---
test_pull_handles_no_remote() {
    local tmpdir
    tmpdir=$(mktemp -d)
    git init "$tmpdir" >/dev/null 2>&1
    cd "$tmpdir"
    git config user.email "test@test.com"
    git config user.name "Test"
    echo "test" > f.md
    git add f.md && git commit -m "init" >/dev/null 2>&1
    life_git_pull "$tmpdir" >/dev/null 2>&1
    local rc=$?
    rm -rf "$tmpdir"
    [ "$rc" -eq 0 ] && pass "pull handles no remote" || fail "pull handles no remote (exit $rc)"
}

# --- Test: pull aborts rebase on conflict ---
test_pull_rebase_abort_on_conflict() {
    TMPDIR_BASE=$(mktemp -d)
    REMOTE="$TMPDIR_BASE/remote.git"
    LOCAL="$TMPDIR_BASE/local"
    LOCAL2="$TMPDIR_BASE/local2"

    git init --bare --initial-branch=main "$REMOTE" >/dev/null 2>&1
    git clone "$REMOTE" "$LOCAL" >/dev/null 2>&1
    cd "$LOCAL"
    git config user.email "test@test.com"
    git config user.name "Test"
    echo "line1" > shared.md
    git add shared.md && git commit -m "init" >/dev/null 2>&1
    git push origin main >/dev/null 2>&1

    # Clone a second copy, make conflicting change
    git clone "$REMOTE" "$LOCAL2" >/dev/null 2>&1
    cd "$LOCAL2"
    git config user.email "test2@test.com"
    git config user.name "Test2"
    echo "conflict-from-remote" > shared.md
    git add shared.md && git commit -m "remote change" >/dev/null 2>&1
    git push origin main >/dev/null 2>&1

    # Make conflicting local change
    cd "$LOCAL"
    echo "conflict-from-local" > shared.md
    git add shared.md && git commit -m "local change" >/dev/null 2>&1

    # Pull should detect conflict, abort rebase, and return 0
    life_git_pull "$LOCAL" >/dev/null 2>&1
    local rc=$?

    # Verify repo is NOT in a broken rebase state
    local rebase_in_progress=0
    [ -d "$LOCAL/.git/rebase-merge" ] || [ -d "$LOCAL/.git/rebase-apply" ] && rebase_in_progress=1

    [ "$rc" -eq 0 ] && [ "$rebase_in_progress" -eq 0 ] \
        && pass "pull aborts rebase on conflict" \
        || fail "pull aborts rebase on conflict (rc=$rc, rebase_stuck=$rebase_in_progress)"
}

# --- Test: LIFE_GIT_SYNC_DISABLED skips sync ---
test_disabled_toggle() {
    setup_test_repos
    echo "should not commit" >> "$LOCAL/skip.md"
    local before
    before=$(git -C "$LOCAL" rev-parse HEAD)
    LIFE_GIT_SYNC_DISABLED=1 life_git_sync "$LOCAL" >/dev/null 2>&1
    local after
    after=$(git -C "$LOCAL" rev-parse HEAD)
    unset LIFE_GIT_SYNC_DISABLED
    [ "$before" = "$after" ] && pass "DISABLED toggle skips sync" || fail "DISABLED toggle skips sync"
}

# --- Run all tests ---
echo "=== life-git-sync.sh tests ==="
test_sync_skips_non_git_dir
test_sync_commits_md_changes
test_sync_noop_no_changes
test_sync_commit_includes_hostname
test_pull_handles_no_remote
test_pull_rebase_abort_on_conflict
test_disabled_toggle

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1
