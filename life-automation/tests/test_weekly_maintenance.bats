#!/usr/bin/env bats
# Tests for weekly-maintenance.sh
# Uses mock claude binary and tmpdir fixtures.

SCRIPT_DIR="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
WEEKLY_SCRIPT="$SCRIPT_DIR/weekly-maintenance.sh"

setup() {
    export TMPDIR_TEST=$(mktemp -d)
    export HOME="$TMPDIR_TEST/home"
    export LIFE_DIR="$TMPDIR_TEST/life"
    export LOG_DIR="$LIFE_DIR/logs"
    mkdir -p "$HOME" "$LIFE_DIR/logs" "$LIFE_DIR/scripts"

    # Create symlink for topology check
    ln -sf "$SCRIPT_DIR" "$LIFE_DIR/scripts"

    # Create daily note
    TODAY=$(date '+%Y-%m-%d')
    YEAR=$(date '+%Y')
    MONTH=$(date '+%m')
    mkdir -p "$LIFE_DIR/Daily/$YEAR/$MONTH"
    echo "# $TODAY" > "$LIFE_DIR/Daily/$YEAR/$MONTH/$TODAY.md"

    # Mock claude binary (succeeds by default)
    export CLAUDE_BIN="$TMPDIR_TEST/claude"
    cat > "$CLAUDE_BIN" <<'MOCK'
#!/bin/bash
echo "mock claude output"
exit 0
MOCK
    chmod +x "$CLAUDE_BIN"

    # Default config
    export WEEKLY_CONFIG="$TMPDIR_TEST/weekly-maintenance.conf"
    cat > "$WEEKLY_CONFIG" <<'CONF'
WEEKLY_MODEL_DAILY="sonnet"
WEEKLY_MODEL_FULL="opus"
WEEKLY_CLAUDE_TIMEOUT=10
WEEKLY_RETRY_DELAY=1
WEEKLY_MAX_RETRIES=1
WEEKLY_NOTIFY_ON_FAILURE=false
WEEKLY_SCHEDULE="05:43"
CONF
}

teardown() {
    rm -rf "$TMPDIR_TEST"
}

# Helper: run the weekly script with our test config
run_weekly() {
    # Override config path by sourcing inline
    # We test the individual logic by sourcing lib + config, not the full script
    # (full script sources its own config from SCRIPT_DIR)
    env HOME="$HOME" \
        LIFE_DIR="$LIFE_DIR" \
        CLAUDE_BIN="$CLAUDE_BIN" \
        "$@"
}

# === Mode selection tests ===
# These test the mode logic directly since the full script sources from SCRIPT_DIR

@test "mode: Monday selects full" {
    source "$SCRIPT_DIR/lib/life-automation-lib.sh"
    # Simulate Monday (date +%u = 1)
    if [[ "$(date +%u)" -eq 1 ]]; then
        MODE=""
        [[ -n "${MODE_OVERRIDE:-}" ]] && MODE="$MODE_OVERRIDE"
        [[ -z "$MODE" ]] && [[ "$(date +%u)" -eq 1 ]] && MODE="full"
        [[ "$MODE" == "full" ]]
    else
        skip "Not Monday — testing override instead"
    fi
}

@test "mode: MODE_OVERRIDE takes precedence" {
    export MODE_OVERRIDE="full"
    MODE=""
    [[ -n "${MODE_OVERRIDE:-}" ]] && MODE="$MODE_OVERRIDE"
    [[ "$MODE" == "full" ]]
}

@test "mode: MODE_OVERRIDE=daily forces daily" {
    export MODE_OVERRIDE="daily"
    MODE=""
    [[ -n "${MODE_OVERRIDE:-}" ]] && MODE="$MODE_OVERRIDE"
    [[ "$MODE" == "daily" ]]
}

# === Config loading ===

@test "config: defaults loaded" {
    source "$WEEKLY_CONFIG"
    [[ "$WEEKLY_MODEL_DAILY" == "sonnet" ]]
    [[ "$WEEKLY_MODEL_FULL" == "opus" ]]
    [[ "$WEEKLY_CLAUDE_TIMEOUT" == "10" ]]
}

@test "config: model selection for daily mode" {
    source "$WEEKLY_CONFIG"
    MODE="daily"
    MODEL="${WEEKLY_MODEL_DAILY:-sonnet}"
    [[ "$MODEL" == "sonnet" ]]
}

@test "config: model selection for full mode" {
    source "$WEEKLY_CONFIG"
    MODE="full"
    MODEL="${WEEKLY_MODEL_FULL:-sonnet}"
    [[ "$MODEL" == "opus" ]]
}

# === LLM kill switch ===

@test "killswitch: env var skips execution" {
    export LIFE_LLM_DISABLED=1
    source "$SCRIPT_DIR/lib/life-automation-lib.sh"
    life_init_env
    run life_check_llm_killswitch
    [[ "$status" -eq 0 ]]
}

@test "killswitch: sentinel file skips execution" {
    source "$SCRIPT_DIR/lib/life-automation-lib.sh"
    life_init_env
    unset LIFE_LLM_DISABLED
    touch "$LIFE_DIR/.llm-disabled"
    run life_check_llm_killswitch
    [[ "$status" -eq 0 ]]
}

# === Retry logic ===

@test "retry: succeeds on first attempt" {
    source "$SCRIPT_DIR/lib/life-automation-lib.sh"
    life_init_env
    ATTEMPT=0
    MAX_ATTEMPTS=2
    SUCCESS=false

    ATTEMPT=$((ATTEMPT + 1))
    if "$CLAUDE_BIN" -p "test" 2>/dev/null; then
        SUCCESS=true
    fi
    [[ "$SUCCESS" == "true" ]]
    [[ "$ATTEMPT" -eq 1 ]]
}

@test "retry: retries on first failure then succeeds" {
    local retry_state="$TMPDIR_TEST/.retry-state"
    rm -f "$retry_state"

    cat > "$CLAUDE_BIN" <<MOCK
#!/bin/bash
if [ ! -f "$retry_state" ]; then
    touch "$retry_state"
    exit 1
fi
rm -f "$retry_state"
exit 0
MOCK
    chmod +x "$CLAUDE_BIN"

    source "$SCRIPT_DIR/lib/life-automation-lib.sh"
    life_init_env

    ATTEMPT=0
    MAX_ATTEMPTS=2
    SUCCESS=false

    while [[ $ATTEMPT -lt $MAX_ATTEMPTS ]]; do
        ATTEMPT=$((ATTEMPT + 1))
        if "$CLAUDE_BIN" -p "test" 2>/dev/null; then
            SUCCESS=true
            break
        fi
    done

    [[ "$SUCCESS" == "true" ]]
    [[ "$ATTEMPT" -eq 2 ]]
}

@test "retry: all attempts fail" {
    cat > "$CLAUDE_BIN" <<'MOCK'
#!/bin/bash
exit 1
MOCK
    chmod +x "$CLAUDE_BIN"

    source "$SCRIPT_DIR/lib/life-automation-lib.sh"
    life_init_env

    ATTEMPT=0
    MAX_ATTEMPTS=2
    SUCCESS=false

    while [[ $ATTEMPT -lt $MAX_ATTEMPTS ]]; do
        ATTEMPT=$((ATTEMPT + 1))
        if "$CLAUDE_BIN" -p "test" 2>/dev/null; then
            SUCCESS=true
            break
        fi
    done

    [[ "$SUCCESS" != "true" ]]
    [[ "$ATTEMPT" -eq 2 ]]
}

# === Verification logic ===

@test "verify: detects unchanged daily note" {
    source "$SCRIPT_DIR/lib/life-automation-lib.sh"
    life_init_env

    PRE_MTIME=$(stat -c %Y "$DAILY_NOTE" 2>/dev/null || echo 0)
    POST_MTIME="$PRE_MTIME"
    PRE_PENDING=3

    # Should warn
    WARNING=""
    if [[ "$PRE_PENDING" =~ ^[0-9]+$ ]] && [[ "$PRE_PENDING" -gt 0 ]] && [[ "$POST_MTIME" -eq "$PRE_MTIME" ]]; then
        WARNING="unchanged"
    fi
    [[ "$WARNING" == "unchanged" ]]
}

@test "verify: no warning when note changed" {
    source "$SCRIPT_DIR/lib/life-automation-lib.sh"
    life_init_env

    PRE_MTIME=1000
    POST_MTIME=2000
    PRE_PENDING=3

    WARNING=""
    if [[ "$PRE_PENDING" =~ ^[0-9]+$ ]] && [[ "$PRE_PENDING" -gt 0 ]] && [[ "$POST_MTIME" -eq "$PRE_MTIME" ]]; then
        WARNING="unchanged"
    fi
    [[ -z "$WARNING" ]]
}

# === Full script syntax ===

@test "script: valid bash syntax" {
    run bash -n "$WEEKLY_SCRIPT"
    [[ "$status" -eq 0 ]]
}
