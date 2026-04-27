#!/bin/bash
# Tests for scripts/auto-deploy.sh
#
# Validates resilience against the 14-hour outage caused by a dirty
# working tree blocking git pull with no alerting or recovery.
#
#   I1: Clean pull succeeds → playbooks run
#   I2: Dirty working tree → detected + alert sent (RED before fix)
#   I3: Network failure (fetch fails) → exits cleanly
#   I4: Already up to date → exits cleanly, no playbooks

set -uo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$TEST_DIR/.." && pwd)"
DEPLOY_SCRIPT="$REPO_DIR/scripts/auto-deploy.sh"

source "$REPO_DIR/scripts/lib/test-harness.sh"
source "$TEST_DIR/lib/watchdog-shims.sh"

echo "=== auto-deploy.sh ==="

_setup_repo() {
    shims_init

    BARE_REMOTE="$SHIM_TMP/remote.git"
    WORK_REPO="$SHIM_TMP/work"
    TELEGRAM_LOG="$SHIM_TMP/telegram.log"

    git init --bare "$BARE_REMOTE" >/dev/null 2>&1
    git clone "$BARE_REMOTE" "$WORK_REPO" >/dev/null 2>&1

    cd "$WORK_REPO"
    git config user.email "test@test.com"
    git config user.name "test"
    echo "init" > README.md
    mkdir -p scripts/lib playbooks
    : > scripts/.env.cluster
    : > playbooks/log-maintenance.yml
    : > playbooks/openclaw-monitoring.yml
    : > playbooks/monitoring.yml
    git add -A && git commit -m "initial" >/dev/null 2>&1
    git push origin master >/dev/null 2>&1

    shims_set_script ansible-playbook <<'BASH'
exit 0
BASH

    shims_set_script ssh <<'BASH'
exit 0
BASH

    # Build a patched copy of auto-deploy that uses our test repo
    TEST_DEPLOY="$SHIM_TMP/auto-deploy-test.sh"
    sed \
        -e "s|^REPO_DIR=.*|REPO_DIR=\"$WORK_REPO\"|" \
        -e "s|^SCRIPT_DIR=.*|SCRIPT_DIR=\"$WORK_REPO/scripts\"|" \
        "$DEPLOY_SCRIPT" > "$TEST_DEPLOY"

    # Inject a telegram shim into the scripts/lib dir
    cat > "$WORK_REPO/scripts/lib/telegram.sh" <<TGEOF
send_telegram() { echo "TELEGRAM: \$*" >> "$TELEGRAM_LOG"; }
TGEOF

    chmod +x "$TEST_DEPLOY"
}

_cleanup() {
    shims_cleanup
    cd "$TEST_DIR"
}

# -- I1: Clean pull succeeds → playbooks run --
run_i1() {
    _setup_repo

    cd "$WORK_REPO"
    echo "change" > somefile.txt
    git add somefile.txt && git commit -m "feat: test change" >/dev/null 2>&1
    git push origin master >/dev/null 2>&1
    git reset --hard HEAD~1 >/dev/null 2>&1

    bash "$TEST_DEPLOY" >/dev/null 2>&1
    local rc=$?

    [ "$rc" -eq 0 ] && \
        pass "I1: clean pull succeeds (exit 0)" || \
        fail "I1: clean pull succeeds" "exit: $rc"

    local ap_calls
    ap_calls=$(shims_call_count ansible-playbook "." 2>/dev/null)
    [ "$ap_calls" -ge 3 ] && \
        pass "I1: playbooks ran ($ap_calls calls)" || \
        fail "I1: playbooks ran" "ansible-playbook called $ap_calls times, expected >=3"

    _cleanup
}

# -- I2: Dirty working tree → detected + alert sent --
run_i2() {
    _setup_repo

    cd "$WORK_REPO"
    echo "remote change" > remotefile.txt
    git add remotefile.txt && git commit -m "feat: remote change" >/dev/null 2>&1
    git push origin master >/dev/null 2>&1
    git reset --hard HEAD~1 >/dev/null 2>&1

    # Dirty the working tree (staged change that conflicts with pull)
    echo "dirty local edit" >> README.md

    bash "$TEST_DEPLOY" >/dev/null 2>&1

    if [ -f "$TELEGRAM_LOG" ] && grep -qi "dirty\|stash" "$TELEGRAM_LOG" 2>/dev/null; then
        pass "I2: dirty tree triggers Telegram alert"
    else
        fail "I2: dirty tree triggers Telegram alert" \
             "no alert found — auto-deploy silently failed"
    fi

    local stash_count
    stash_count=$(cd "$WORK_REPO" && git stash list 2>/dev/null | wc -l)
    [ "$stash_count" -ge 1 ] && \
        pass "I2: dirty changes stashed for recovery" || \
        fail "I2: dirty changes stashed for recovery" "stash count: $stash_count"

    _cleanup
}

# -- I3: Network failure (fetch fails) → exits cleanly --
run_i3() {
    _setup_repo

    cd "$WORK_REPO"
    git remote set-url origin "https://unreachable.invalid/repo.git"

    bash "$TEST_DEPLOY" >/dev/null 2>&1
    local rc=$?

    [ "$rc" -eq 0 ] && \
        pass "I3: network failure exits cleanly (exit 0)" || \
        fail "I3: network failure exits cleanly" "exit: $rc"

    _cleanup
}

# -- I4: Already up to date → no-op --
run_i4() {
    _setup_repo

    bash "$TEST_DEPLOY" >/dev/null 2>&1
    local rc=$?

    [ "$rc" -eq 0 ] && \
        pass "I4: up-to-date exits cleanly (exit 0)" || \
        fail "I4: up-to-date exits cleanly" "exit: $rc"

    local ap_calls
    ap_calls=$(shims_call_count ansible-playbook "." 2>/dev/null)
    [ "$ap_calls" -eq 0 ] && \
        pass "I4: no playbooks run when up to date" || \
        fail "I4: no playbooks run when up to date" "ansible-playbook called $ap_calls times"

    _cleanup
}

run_i1
run_i2
run_i3
run_i4

test_summary
