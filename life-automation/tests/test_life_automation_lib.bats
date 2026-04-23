#!/usr/bin/env bats
# Tests for lib/life-automation-lib.sh shared functions.

LIB_DIR="$(cd "$(dirname "$BATS_TEST_FILENAME")/../lib" && pwd)"
LIB_FILE="$LIB_DIR/life-automation-lib.sh"

setup() {
    export TMPDIR_TEST=$(mktemp -d)
    export HOME="$TMPDIR_TEST/home"
    export LIFE_DIR="$TMPDIR_TEST/life"
    mkdir -p "$HOME" "$LIFE_DIR/logs"

    # Stub claude binary
    export CLAUDE_BIN="$TMPDIR_TEST/claude"
    echo '#!/bin/bash' > "$CLAUDE_BIN"
    chmod +x "$CLAUDE_BIN"

    source "$LIB_FILE"
}

teardown() {
    rm -rf "$TMPDIR_TEST"
}

# === life_init_env ===

@test "life_init_env sets core variables" {
    life_init_env
    [[ -n "$TODAY" ]]
    [[ -n "$YEAR" ]]
    [[ -n "$MONTH" ]]
    [[ "$LOG_DIR" == "$LIFE_DIR/logs" ]]
    [[ "$DAILY_NOTE" == *"$TODAY.md" ]]
}

@test "life_init_env creates LOG_DIR" {
    rm -rf "$LIFE_DIR/logs"
    life_init_env
    [[ -d "$LOG_DIR" ]]
}

@test "life_init_env respects LIFE_DIR override" {
    export LIFE_DIR="$TMPDIR_TEST/custom-life"
    mkdir -p "$LIFE_DIR"
    life_init_env
    [[ "$LOG_DIR" == "$TMPDIR_TEST/custom-life/logs" ]]
}

@test "life_init_env respects CLAUDE_BIN override" {
    export CLAUDE_BIN="/opt/custom/claude"
    life_init_env
    [[ "$CLAUDE_BIN" == "/opt/custom/claude" ]]
}

@test "life_init_env sets cron-safe PATH" {
    life_init_env
    [[ "$PATH" == *".local/bin"* ]]
    [[ "$PATH" == *"/usr/local/bin"* ]]
}

# === life_log + life_log_file ===

@test "life_log_file returns correct path" {
    life_init_env
    local result
    result=$(life_log_file "test-tag")
    [[ "$result" == "$LOG_DIR/test-tag.log" ]]
}

@test "life_log writes timestamped message to file" {
    life_init_env
    life_log "mytag" "hello world"
    local logfile="$LOG_DIR/mytag.log"
    [[ -f "$logfile" ]]
    grep -q '\[mytag\] hello world' "$logfile"
}

@test "life_log appends to existing log" {
    life_init_env
    life_log "mytag" "first"
    life_log "mytag" "second"
    local count
    count=$(wc -l < "$LOG_DIR/mytag.log")
    [[ "$count" -eq 2 ]]
}

@test "life_log_file fails without life_init_env" {
    unset LOG_DIR
    run life_log_file "tag"
    [[ "$status" -ne 0 ]]
}

# === life_check_topology ===

@test "life_check_topology passes with symlink" {
    life_init_env
    ln -s /tmp "$LIFE_DIR/scripts"
    run life_check_topology
    [[ "$status" -eq 0 ]]
}

@test "life_check_topology fails with real directory" {
    life_init_env
    mkdir -p "$LIFE_DIR/scripts"
    run life_check_topology
    [[ "$status" -eq 1 ]]
    [[ "$output" == *"not a symlink"* ]]
}

@test "life_check_topology fails when missing" {
    life_init_env
    run life_check_topology
    [[ "$status" -eq 1 ]]
}

# === life_check_llm_killswitch ===

@test "llm killswitch detects env var" {
    life_init_env
    export LIFE_LLM_DISABLED=1
    run life_check_llm_killswitch
    [[ "$status" -eq 0 ]]
    [[ "$output" == *"kill switch active"* ]]
}

@test "llm killswitch detects sentinel file" {
    life_init_env
    unset LIFE_LLM_DISABLED
    touch "$LIFE_DIR/.llm-disabled"
    run life_check_llm_killswitch
    [[ "$status" -eq 0 ]]
    [[ "$output" == *"sentinel"* ]]
}

@test "llm killswitch returns 1 when not active" {
    life_init_env
    unset LIFE_LLM_DISABLED
    run life_check_llm_killswitch
    [[ "$status" -eq 1 ]]
}

# === life_rotate_logs ===

@test "rotate_logs deletes old error files" {
    life_init_env
    local old_error="$LOG_DIR/test-errors-old.json"
    touch -d "60 days ago" "$old_error"
    life_rotate_logs "test" 1048576 30
    [[ ! -f "$old_error" ]]
}

@test "rotate_logs rotates oversized log" {
    life_init_env
    local logfile="$LOG_DIR/test.log"
    # Create file > 100 bytes
    dd if=/dev/zero of="$logfile" bs=200 count=1 2>/dev/null
    life_rotate_logs "test" 100 30
    [[ -f "${logfile}.old" ]]
}

@test "rotate_logs keeps small log" {
    life_init_env
    local logfile="$LOG_DIR/test.log"
    echo "small" > "$logfile"
    life_rotate_logs "test" 1048576 30
    [[ ! -f "${logfile}.old" ]]
    [[ -f "$logfile" ]]
}

# === life_acquire_lock ===

@test "acquire_lock succeeds when free" {
    life_init_env
    life_acquire_lock "$TMPDIR_TEST/test.lock"
    [[ $? -eq 0 ]]
}

@test "acquire_lock fails when held" {
    life_init_env
    local lock_file="$TMPDIR_TEST/test.lock"
    # Hold lock in background subshell
    (
        exec 9>"$lock_file"
        flock -n 9
        sleep 5
    ) &
    local bg_pid=$!
    sleep 0.2

    run life_acquire_lock "$lock_file"
    [[ "$status" -eq 1 ]]
    [[ "$output" == *"Lock held"* ]]

    kill "$bg_pid" 2>/dev/null || true
    wait "$bg_pid" 2>/dev/null || true
}

# === life_require_daily_note ===

@test "require_daily_note passes when file exists" {
    life_init_env
    mkdir -p "$(dirname "$DAILY_NOTE")"
    touch "$DAILY_NOTE"
    run life_require_daily_note
    [[ "$status" -eq 0 ]]
}

@test "require_daily_note fails when missing" {
    life_init_env
    run life_require_daily_note
    [[ "$status" -eq 1 ]]
    [[ "$output" == *"No daily note"* ]]
}

# === life_require_claude_cli ===

@test "require_claude_cli passes with executable" {
    life_init_env
    run life_require_claude_cli
    [[ "$status" -eq 0 ]]
}

@test "require_claude_cli fails when missing" {
    export CLAUDE_BIN="$TMPDIR_TEST/nonexistent"
    life_init_env
    run life_require_claude_cli
    [[ "$status" -eq 1 ]]
    [[ "$output" == *"not found"* ]]
}

@test "require_claude_cli fails when not executable" {
    life_init_env
    chmod -x "$CLAUDE_BIN"
    run life_require_claude_cli
    [[ "$status" -eq 1 ]]
}

# === life_notify_telegram ===

@test "notify_telegram returns error without telegram lib" {
    life_init_env
    unset TELEGRAM_BOT_TOKEN
    unset TELEGRAM_CHAT_ID
    # Override HOME so telegram lib path doesn't exist
    export HOME="$TMPDIR_TEST/nonexistent-home"
    run life_notify_telegram "test message"
    [[ "$status" -eq 1 ]]
    [[ "$output" == *"not found"* ]]
}
