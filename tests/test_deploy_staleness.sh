#!/bin/bash
# Tests for scripts/deploy-staleness-check.sh
#
#   S1: Fresh heartbeat → no alert
#   S2: Stale heartbeat → alert sent
#   S3: SSH fails → alert sent
#   S4: Dedup works (no repeat alert within 1 hour)

set -uo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$TEST_DIR/.." && pwd)"

source "$REPO_DIR/scripts/lib/test-harness.sh"
source "$TEST_DIR/lib/watchdog-shims.sh"

echo "=== deploy-staleness-check.sh ==="

_setup() {
    shims_init

    TELEGRAM_LOG="$SHIM_TMP/telegram.log"
    STATE_DIR="$SHIM_TMP/state/watchdog"
    mkdir -p "$STATE_DIR" "$SHIM_TMP/scripts/lib"

    cat > "$SHIM_TMP/scripts/.env.cluster" <<ENV
TELEGRAM_BOT_TOKEN=test
TELEGRAM_CHAT_ID=test
MASTER_HOST=localhost
ENV

    cat > "$SHIM_TMP/scripts/lib/telegram.sh" <<TGEOF
send_telegram() { echo "TELEGRAM: \$*" >> "$TELEGRAM_LOG"; }
TGEOF

    export XDG_STATE_HOME="$SHIM_TMP/state"
    export STALE_THRESHOLD=60

    TEST_SCRIPT="$SHIM_TMP/staleness-test.sh"
    sed \
        -e "s|^SCRIPT_DIR=.*|SCRIPT_DIR=\"$SHIM_TMP/scripts\"|" \
        -e "s|^MASTER_HOST=.*|MASTER_HOST=\"localhost\"|" \
        "$REPO_DIR/scripts/deploy-staleness-check.sh" > "$TEST_SCRIPT"
    chmod +x "$TEST_SCRIPT"
}

_cleanup() {
    shims_cleanup
    cd "$TEST_DIR"
}

# -- S1: Fresh heartbeat → no alert --
run_s1() {
    _setup

    NOW=$(date +%s)
    shims_set_script ssh <<BASH
echo "$NOW"
BASH

    bash "$TEST_SCRIPT" >/dev/null 2>&1

    if [ ! -f "$TELEGRAM_LOG" ] || [ ! -s "$TELEGRAM_LOG" ]; then
        pass "S1: fresh heartbeat → no alert"
    else
        fail "S1: fresh heartbeat → no alert" "alert was sent: $(cat "$TELEGRAM_LOG")"
    fi

    _cleanup
}

# -- S2: Stale heartbeat → alert --
run_s2() {
    _setup

    OLD_TS=$(( $(date +%s) - 3600 ))
    shims_set_script ssh <<BASH
echo "$OLD_TS"
BASH

    bash "$TEST_SCRIPT" >/dev/null 2>&1

    if [ -f "$TELEGRAM_LOG" ] && grep -qi "stale" "$TELEGRAM_LOG" 2>/dev/null; then
        pass "S2: stale heartbeat → alert sent"
    else
        fail "S2: stale heartbeat → alert sent" "no alert found"
    fi

    _cleanup
}

# -- S3: SSH fails → alert --
run_s3() {
    _setup

    shims_set_script ssh <<'BASH'
exit 1
BASH

    bash "$TEST_SCRIPT" >/dev/null 2>&1

    if [ -f "$TELEGRAM_LOG" ] && grep -qi "stale" "$TELEGRAM_LOG" 2>/dev/null; then
        pass "S3: SSH failure → alert sent"
    else
        fail "S3: SSH failure → alert sent" "no alert found"
    fi

    _cleanup
}

# -- S4: Dedup works --
run_s4() {
    _setup

    OLD_TS=$(( $(date +%s) - 3600 ))
    shims_set_script ssh <<BASH
echo "$OLD_TS"
BASH

    bash "$TEST_SCRIPT" >/dev/null 2>&1
    local first_count
    first_count=$(wc -l < "$TELEGRAM_LOG" 2>/dev/null || echo "0")

    bash "$TEST_SCRIPT" >/dev/null 2>&1
    local second_count
    second_count=$(wc -l < "$TELEGRAM_LOG" 2>/dev/null || echo "0")

    [[ "$second_count" -eq "$first_count" ]] && \
        pass "S4: dedup prevents second alert" || \
        fail "S4: dedup prevents second alert" "first=$first_count second=$second_count"

    _cleanup
}

run_s1
run_s2
run_s3
run_s4

test_summary
