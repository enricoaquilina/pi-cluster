#!/bin/bash
# Shared structured JSON logging for cluster scripts.
# Source this file in scripts: source "$SCRIPT_DIR/lib/log.sh"
#
# Provides: log() — outputs JSON to stdout and appends to LOG_FILE.
# Set LOG_FILE before sourcing, or it defaults to /dev/null.
#
# Usage:
#   log "Backup started"              → {"ts":"...","level":"info","msg":"Backup started","script":"openclaw-backup.sh"}
#   log "Disk full" "error"           → {"ts":"...","level":"error","msg":"Disk full","script":"openclaw-backup.sh"}
#   log "Retrying" "warn"             → {"ts":"...","level":"warn","msg":"Retrying","script":"openclaw-backup.sh"}

_LOG_SCRIPT_NAME="$(basename "${BASH_SOURCE[1]:-${0:-unknown}}")"

log() {
    local msg="${1:-}" level="${2:-info}"
    # Escape quotes and backslashes in message for valid JSON
    msg="${msg//\\/\\\\}"
    msg="${msg//\"/\\\"}"
    msg="${msg//$'\n'/\\n}"
    printf '{"ts":"%s","level":"%s","msg":"%s","script":"%s"}\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$level" "$msg" "$_LOG_SCRIPT_NAME" \
        | tee -a "${LOG_FILE:-/dev/null}"
}
