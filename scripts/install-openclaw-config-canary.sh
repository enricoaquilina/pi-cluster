#!/bin/bash
set -euo pipefail
# install-openclaw-config-canary.sh — deploy the hourly schema canary
# (systemd --user timer) onto the live host.
#
# Installs:
#   ~/.local/bin/openclaw-config-canary
#   ~/.local/bin/openclaw-config-validate   (if not already present — the
#                                            canary wrapper invokes it)
#   ~/.config/systemd/user/openclaw-config-canary.service
#   ~/.config/systemd/user/openclaw-config-canary.timer
#
# Idempotent. Safe to re-run. Enables --now the timer unless --no-enable
# is passed.
#
# Usage:
#   scripts/install-openclaw-config-canary.sh              # install + enable
#   scripts/install-openclaw-config-canary.sh --no-enable  # install only

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

CANARY_SRC="$SCRIPT_DIR/openclaw-config-canary.sh"
VALIDATOR_SRC="$SCRIPT_DIR/openclaw-config-validate.sh"
SERVICE_SRC="$REPO_DIR/templates/openclaw-config-canary.service"
TIMER_SRC="$REPO_DIR/templates/openclaw-config-canary.timer"

BIN_DIR="${OPENCLAW_CANARY_BIN_DIR:-$HOME/.local/bin}"
UNIT_DIR="${OPENCLAW_CANARY_UNIT_DIR:-$HOME/.config/systemd/user}"

CANARY_DEST="$BIN_DIR/openclaw-config-canary"
VALIDATOR_DEST="$BIN_DIR/openclaw-config-validate"
SERVICE_DEST="$UNIT_DIR/openclaw-config-canary.service"
TIMER_DEST="$UNIT_DIR/openclaw-config-canary.timer"

ENABLE=true

while [ $# -gt 0 ]; do
    case "$1" in
        --no-enable) ENABLE=false; shift ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

log() { echo "[install-openclaw-config-canary] $*"; }

# --- Preflight ---------------------------------------------------------
for src in "$CANARY_SRC" "$VALIDATOR_SRC" "$SERVICE_SRC" "$TIMER_SRC"; do
    if [ ! -f "$src" ]; then
        echo "source missing: $src" >&2
        exit 1
    fi
done

if ! bash -n "$CANARY_SRC"; then
    echo "canary source has bash syntax errors — refusing to install" >&2
    exit 1
fi
if ! bash -n "$VALIDATOR_SRC"; then
    echo "validator source has bash syntax errors — refusing to install" >&2
    exit 1
fi

mkdir -p "$BIN_DIR" "$UNIT_DIR"

# --- Install bin files -------------------------------------------------
install_if_changed() {
    local src="$1" dest="$2"
    if [ -f "$dest" ] && cmp -s "$src" "$dest"; then
        log "$dest already up to date"
        return 0
    fi
    if [ -f "$dest" ]; then
        local backup
        backup="${dest}.bak-$(date +%Y%m%d-%H%M%S)"
        cp "$dest" "$backup"
        log "backed up existing copy to $backup"
    fi
    install -m 755 "$src" "$dest"
    log "installed $src -> $dest"
}

install_if_changed "$CANARY_SRC" "$CANARY_DEST"

# Only install the validator if it isn't already deployed — it has its
# own release path and we don't want to shadow a newer copy.
if [ ! -f "$VALIDATOR_DEST" ]; then
    install -m 755 "$VALIDATOR_SRC" "$VALIDATOR_DEST"
    log "installed $VALIDATOR_SRC -> $VALIDATOR_DEST (fresh)"
else
    log "$VALIDATOR_DEST already present — leaving untouched"
fi

# --- Install unit files ------------------------------------------------
install_unit_if_changed() {
    local src="$1" dest="$2"
    if [ -f "$dest" ] && cmp -s "$src" "$dest"; then
        log "$dest already up to date"
        return 0
    fi
    if [ -f "$dest" ]; then
        local backup
        backup="${dest}.bak-$(date +%Y%m%d-%H%M%S)"
        cp "$dest" "$backup"
        log "backed up existing unit to $backup"
    fi
    install -m 644 "$src" "$dest"
    log "installed $src -> $dest"
}

install_unit_if_changed "$SERVICE_SRC" "$SERVICE_DEST"
install_unit_if_changed "$TIMER_SRC" "$TIMER_DEST"

# --- Enable timer ------------------------------------------------------
if ! command -v systemctl >/dev/null 2>&1; then
    log "systemctl not found — unit files installed but not enabled"
    exit 0
fi

if ! systemctl --user daemon-reload 2>/dev/null; then
    log "systemctl --user daemon-reload failed (no user bus?) — skipping enable"
    log "re-run this installer from an interactive session, or use 'loginctl enable-linger $(whoami)'"
    exit 0
fi
log "systemctl --user daemon-reload ok"

if ! $ENABLE; then
    log "--no-enable: skipping enable/start"
    exit 0
fi

systemctl --user enable --now openclaw-config-canary.timer
log "enabled + started openclaw-config-canary.timer"

# Warn if linger is off — user timers stop when the session ends otherwise.
if command -v loginctl >/dev/null 2>&1; then
    linger="$(loginctl show-user "$(whoami)" -p Linger --value 2>/dev/null || echo unknown)"
    if [ "$linger" != "yes" ]; then
        log "WARN: user linger is '$linger' — timer will stop at logout."
        log "      run: sudo loginctl enable-linger $(whoami)"
    else
        log "user linger: enabled"
    fi
fi

# Show the next tick so the operator can eyeball it.
systemctl --user list-timers openclaw-config-canary.timer --no-pager 2>/dev/null || true

log "done."
