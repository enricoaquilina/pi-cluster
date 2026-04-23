#!/bin/bash
# Shared functions for life-automation scripts.
# Source this file — functions only, no side effects on source.
#
# Usage:
#   SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   source "$SCRIPT_DIR/lib/life-automation-lib.sh"

# --- Environment setup (cron safety) ---
# Sets: PATH, HOME, LANG, PYTHONIOENCODING, LIFE_DIR, CLAUDE_BIN, LOG_DIR,
#       TODAY, YEAR, MONTH, DAILY_NOTE
# Creates LOG_DIR if missing.
life_init_env() {
    export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
    export HOME="${HOME:-/home/enrico}"
    export LANG="en_US.UTF-8"
    export PYTHONIOENCODING="utf-8"

    LIFE_DIR="${LIFE_DIR:-$HOME/life}"
    CLAUDE_BIN="${CLAUDE_BIN:-$HOME/.local/bin/claude}"
    LOG_DIR="$LIFE_DIR/logs"

    TODAY=$(date '+%Y-%m-%d')
    YEAR=$(date '+%Y')
    MONTH=$(date '+%m')
    DAILY_NOTE="$LIFE_DIR/Daily/$YEAR/$MONTH/$TODAY.md"

    mkdir -p "$LOG_DIR"
}

# --- Logging ---
# Returns the log file path for a given tag.
life_log_file() {
    local tag="${1:?usage: life_log_file TAG}"
    echo "${LOG_DIR:?life_init_env not called}/$tag.log"
}

# Logs a timestamped message to stdout and the tag's log file.
life_log() {
    local tag="${1:?usage: life_log TAG message...}"
    shift
    local logfile
    logfile="$(life_log_file "$tag")"
    echo "$(date '+%Y-%m-%d %H:%M:%S') [$tag] $*" | tee -a "$logfile"
}

# --- Topology check ---
# Verifies ~/life/scripts is a symlink (not a real directory).
# Returns 0 on success, 1 on failure (with message to stderr).
life_check_topology() {
    local scripts_dir="${LIFE_DIR:?life_init_env not called}/scripts"
    if [[ ! -L "$scripts_dir" ]]; then
        echo "ERROR: $scripts_dir is not a symlink; topology has drifted" >&2
        echo "Expected: symlink to ~/pi-cluster/life-automation/" >&2
        return 1
    fi
    return 0
}

# --- LLM kill switch ---
# Checks env var and sentinel file. Exports LIFE_LLM_DISABLED=1 if active.
# Returns 0 if kill switch is active, 1 if LLM is allowed.
life_check_llm_killswitch() {
    if [[ -n "${LIFE_LLM_DISABLED:-}" ]]; then
        echo "LLM kill switch active (env LIFE_LLM_DISABLED=$LIFE_LLM_DISABLED)" >&2
        return 0
    elif [[ -f "${LIFE_DIR:?life_init_env not called}/.llm-disabled" ]]; then
        export LIFE_LLM_DISABLED=1
        echo "LLM kill switch active (sentinel $LIFE_DIR/.llm-disabled)" >&2
        return 0
    fi
    return 1
}

# --- Log rotation ---
# Rotates the log file for a tag if it exceeds max_bytes.
# Cleans error files older than max_days.
# Args: tag [max_bytes=1048576] [max_days=30]
life_rotate_logs() {
    local tag="${1:?usage: life_rotate_logs TAG [max_bytes] [max_days]}"
    local max_bytes="${2:-1048576}"
    local max_days="${3:-30}"
    local logfile
    logfile="$(life_log_file "$tag")"

    find "$LOG_DIR" -name "${tag}-errors-*.json" -mtime +"$max_days" -delete 2>/dev/null || true

    if [[ -f "$logfile" ]] && [[ $(wc -c < "$logfile") -gt "$max_bytes" ]]; then
        mv "$logfile" "${logfile}.old"
        life_log "$tag" "Rotated $tag.log (exceeded ${max_bytes} bytes)"
    fi
}

# --- Lock acquisition ---
# Acquires an exclusive lock on the given file using fd 9.
# Returns 0 on success, 1 if lock is held by another process.
# Caller must handle the return value (e.g., exit 0 if already running).
life_acquire_lock() {
    local lock_file="${1:?usage: life_acquire_lock LOCK_FILE}"
    exec 9>"$lock_file"
    if ! flock -n 9; then
        echo "Lock held: $lock_file — another instance running" >&2
        return 1
    fi
    return 0
}

# --- Preflight: daily note ---
# Returns 0 if today's daily note exists, 1 otherwise.
life_require_daily_note() {
    if [[ ! -f "${DAILY_NOTE:?life_init_env not called}" ]]; then
        echo "No daily note for ${TODAY:?} at $DAILY_NOTE" >&2
        return 1
    fi
    return 0
}

# --- Preflight: claude CLI ---
# Returns 0 if claude binary exists and is executable, 1 otherwise.
life_require_claude_cli() {
    if [[ ! -x "${CLAUDE_BIN:?life_init_env not called}" ]]; then
        echo "Claude CLI not found at $CLAUDE_BIN" >&2
        return 1
    fi
    return 0
}

# --- Telegram notification ---
# Sends a message via Telegram. Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
# Silently no-ops if credentials are unset or curl fails.
life_notify_telegram() {
    local msg="${1:-}"
    local telegram_lib="$HOME/pi-cluster/scripts/lib/telegram.sh"
    if ! type send_telegram >/dev/null 2>&1; then
        if [[ -f "$telegram_lib" ]]; then
            # shellcheck source=/dev/null
            source "$telegram_lib" 2>/dev/null || return 0
        else
            return 0
        fi
    fi
    send_telegram "$msg" 2>/dev/null || true
}
