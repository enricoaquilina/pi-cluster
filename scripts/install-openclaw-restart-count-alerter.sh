#!/bin/bash
set -euo pipefail
# install-openclaw-restart-count-alerter.sh — deploy the sliding-window
# restart-count alerter as a systemd --user timer.
#
# Installs:
#   ~/.local/bin/openclaw-restart-count-alerter
#   ~/.config/systemd/user/openclaw-restart-count-alerter.service
#   ~/.config/systemd/user/openclaw-restart-count-alerter.timer
#
# Idempotent. Safe to re-run.
#
# Usage:
#   scripts/install-openclaw-restart-count-alerter.sh              # install + enable
#   scripts/install-openclaw-restart-count-alerter.sh --no-enable  # install only

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

SRC_SCRIPT="$SCRIPT_DIR/openclaw-restart-count-alerter.sh"
SRC_SERVICE="$REPO_DIR/templates/openclaw-restart-count-alerter.service"
SRC_TIMER="$REPO_DIR/templates/openclaw-restart-count-alerter.timer"

BIN_DIR="${RESTART_COUNT_BIN_DIR:-$HOME/.local/bin}"
UNIT_DIR="${RESTART_COUNT_UNIT_DIR:-$HOME/.config/systemd/user}"

DEST_SCRIPT="$BIN_DIR/openclaw-restart-count-alerter"
DEST_SERVICE="$UNIT_DIR/openclaw-restart-count-alerter.service"
DEST_TIMER="$UNIT_DIR/openclaw-restart-count-alerter.timer"

ENABLE=true

while [ $# -gt 0 ]; do
    case "$1" in
        --no-enable) ENABLE=false; shift ;;
        -h|--help)
            grep '^#' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
            exit 0
            ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

log() { echo "[install-restart-count-alerter] $*"; }

for src in "$SRC_SCRIPT" "$SRC_SERVICE" "$SRC_TIMER"; do
    if [ ! -f "$src" ]; then
        echo "source missing: $src" >&2
        exit 1
    fi
done
if ! bash -n "$SRC_SCRIPT"; then
    echo "alerter source has bash syntax errors — refusing to install" >&2
    exit 1
fi

mkdir -p "$BIN_DIR" "$UNIT_DIR"

install_if_changed() {
    local src="$1" dest="$2" mode="${3:-755}"
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
    install -m "$mode" "$src" "$dest"
    log "installed $src -> $dest"
}

install_if_changed "$SRC_SCRIPT" "$DEST_SCRIPT" 755
install_if_changed "$SRC_SERVICE" "$DEST_SERVICE" 644
install_if_changed "$SRC_TIMER" "$DEST_TIMER" 644

if ! command -v systemctl >/dev/null 2>&1; then
    log "systemctl not found — unit files installed but not enabled"
    exit 0
fi

if ! systemctl --user daemon-reload 2>/dev/null; then
    log "systemctl --user daemon-reload failed (no user bus?) — skipping enable"
    exit 0
fi
log "systemctl --user daemon-reload ok"

if [ "$ENABLE" = false ]; then
    log "--no-enable: skipping enable/start"
    exit 0
fi

systemctl --user enable --now openclaw-restart-count-alerter.timer
log "enabled + started openclaw-restart-count-alerter.timer"

if command -v loginctl >/dev/null 2>&1; then
    linger="$(loginctl show-user "$(whoami)" -p Linger --value 2>/dev/null || echo unknown)"
    if [ "$linger" != "yes" ]; then
        log "WARN: user linger is '$linger' — timer will stop at logout."
        log "      run: sudo loginctl enable-linger $(whoami)"
    else
        log "user linger: enabled"
    fi
fi

systemctl --user list-timers openclaw-restart-count-alerter.timer --no-pager 2>/dev/null || true

log "done."
