#!/bin/bash
# Shared env var validation for cluster scripts.
# Source after loading .env.cluster, then call require_env or warn_env.
#
# Usage:
#   source "$SCRIPT_DIR/lib/env-validate.sh"
#   require_env MC_API_KEY OPENCLAW_GATEWAY_TOKEN
#   warn_env TELEGRAM_BOT_TOKEN

require_env() {
    local missing=()
    for var in "$@"; do
        [ -z "${!var:-}" ] && missing+=("$var")
    done
    if [ ${#missing[@]} -gt 0 ]; then
        echo "FATAL: $(basename "$0"): missing env vars: ${missing[*]}" >&2
        exit 1
    fi
}

warn_env() {
    for var in "$@"; do
        [ -z "${!var:-}" ] && echo "WARN: $(basename "$0"): $var not set" >&2
    done
}
