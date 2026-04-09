#!/bin/bash
# Shim helpers for watchdog + validator tests.
#
# Creates a tmpdir with a "bin/" on PATH containing fake versions of docker,
# curl, logger, sudo, systemctl, mongosh. Each shim records its invocations
# to $CALLS_DIR/<name>.log so tests can assert what the watchdog called.
#
# Also provides scratch state/lock/alert-cache paths so the watchdog under
# test never touches real files.
#
# Usage:
#   source "$SCRIPT_DIR/lib/watchdog-shims.sh"
#   shims_init           # creates tmpdir, sets PATH, exports WATCHDOG_STATE etc.
#   shims_set docker exit=0 stdout="openclaw-openclaw-gateway-1"
#   shims_set curl 'url=http://localhost:18789/healthz exit=0 stdout={"ok":true}'
#   shims_cleanup        # removes tmpdir

shims_init() {
    SHIM_TMP="$(mktemp -d -t watchdog-shim.XXXXXX)"
    SHIM_BIN="$SHIM_TMP/bin"
    CALLS_DIR="$SHIM_TMP/calls"
    STATE_DIR="$SHIM_TMP/state"
    mkdir -p "$SHIM_BIN" "$CALLS_DIR" "$STATE_DIR"
    # Shims run as child processes, so every scratch path must be exported.
    export SHIM_TMP SHIM_BIN CALLS_DIR STATE_DIR

    # Scratch paths for the watchdog under test.
    export WATCHDOG_STATE="$STATE_DIR/state.json"
    export WATCHDOG_LOCK_FILE="$STATE_DIR/lock"
    export WATCHDOG_ALERT_CACHE="$STATE_DIR/last_alert.json"
    export MC_DIR="$SHIM_TMP/mc"
    export GW_DIR="$SHIM_TMP/gw"
    mkdir -p "$MC_DIR" "$GW_DIR"
    # Minimal env files so the watchdog's grep calls don't blow up.
    : > "$MC_DIR/docker-compose.yml"
    echo "OPENCLAW_GATEWAY_TOKEN=shim-expected-token" > "$GW_DIR/.env"

    # Neutralise real network/MC posting by default.
    export MC_API_URL="http://127.0.0.1:0/api"
    export MC_API_KEY="shim-key"
    export TELEGRAM_BOT_TOKEN=""
    export TELEGRAM_CHAT_ID=""
    export HEAVY_IP="127.0.0.1"

    # Create default shims. Tests override per-case via shims_set_script.
    _shim_default docker
    _shim_default curl
    _shim_default logger
    _shim_default sudo
    _shim_default systemctl
    _shim_default mongosh

    # Make the shim bin win on PATH.
    export PATH="$SHIM_BIN:$PATH"
    # Make sure `command -v` finds flock (real) — we never shim it.
}

# Create a shim that just records args and exits 0.
_shim_default() {
    local name="$1"
    cat > "$SHIM_BIN/$name" <<'SHIM'
#!/bin/bash
printf '%q ' "$0" "$@" >> "$CALLS_DIR/$(basename "$0").log"
printf '\n' >> "$CALLS_DIR/$(basename "$0").log"
exit 0
SHIM
    chmod +x "$SHIM_BIN/$name"
}

# Replace a shim with custom behaviour. Script body read from stdin.
# Example:
#   shims_set_script docker <<'BASH'
#     case "$*" in
#       *"inspect"*) echo "OPENCLAW_GATEWAY_TOKEN=shim-expected-token";;
#       *"ps"*) echo "openclaw-openclaw-gateway-1";;
#     esac
#     exit 0
#   BASH
shims_set_script() {
    local name="$1"
    local target="$SHIM_BIN/$name"
    {
        echo '#!/bin/bash'
        echo 'printf "%q " "$0" "$@" >> "$CALLS_DIR/$(basename "$0").log"'
        echo 'printf "\n" >> "$CALLS_DIR/$(basename "$0").log"'
        cat
    } > "$target"
    chmod +x "$target"
}

# Return the call log for a shim (stdout: lines of invocations).
shims_calls() {
    local name="$1"
    cat "$CALLS_DIR/$name.log" 2>/dev/null || true
}

# Count lines in a shim's call log that match a grep pattern.
shims_call_count() {
    local name="$1" pattern="$2"
    grep -c -- "$pattern" "$CALLS_DIR/$name.log" 2>/dev/null || echo 0
}

# Assert a shim was called at least once with args matching the pattern.
shims_assert_called() {
    local name="$1" pattern="$2" msg="${3:-$name called with $pattern}"
    if grep -q -- "$pattern" "$CALLS_DIR/$name.log" 2>/dev/null; then
        pass "$msg"
    else
        fail "$msg" "no matching call in $CALLS_DIR/$name.log"
    fi
}

# Assert a shim was NEVER called with args matching the pattern.
shims_assert_not_called() {
    local name="$1" pattern="$2" msg="${3:-$name NOT called with $pattern}"
    if grep -q -- "$pattern" "$CALLS_DIR/$name.log" 2>/dev/null; then
        fail "$msg" "unexpected call: $(grep -- "$pattern" "$CALLS_DIR/$name.log" | head -1)"
    else
        pass "$msg"
    fi
}

shims_cleanup() {
    [ -n "${SHIM_TMP:-}" ] && rm -rf "$SHIM_TMP"
    unset SHIM_TMP SHIM_BIN CALLS_DIR STATE_DIR
}
