#!/bin/bash
set -euo pipefail
# install-mc-watchdog.sh — deploy the hardened mission-control-watchdog
# onto the live host (heavy).
#
# Context: ~/.local/bin/mission-control-watchdog is historically a *copy*
# of scripts/mission-control-watchdog.sh (set up by an ansible play that
# doesn't exist any more), so a plain `git pull` on the live pi-cluster
# clone does NOT update it. Running this script copies the current repo
# version into place, ensures it's executable, and optionally re-enables
# a paused cron line.
#
# Idempotent. Safe to re-run. Exits 0 if nothing needed to change.
#
# Usage:
#   scripts/install-mc-watchdog.sh              # install + print cron status
#   scripts/install-mc-watchdog.sh --enable-cron  # also uncomment a paused line

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$SCRIPT_DIR/mission-control-watchdog.sh"
DEST="${MC_WATCHDOG_INSTALL_PATH:-$HOME/.local/bin/mission-control-watchdog}"
ENABLE_CRON=false

while [ $# -gt 0 ]; do
    case "$1" in
        --enable-cron) ENABLE_CRON=true; shift ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

log() { echo "[install-mc-watchdog] $*"; }

if [ ! -f "$SRC" ]; then
    echo "source script missing: $SRC" >&2
    exit 1
fi

# Syntax-check before overwriting the live copy.
if ! bash -n "$SRC"; then
    echo "source script has bash syntax errors — refusing to install" >&2
    exit 1
fi

mkdir -p "$(dirname "$DEST")"

if [ -f "$DEST" ] && cmp -s "$SRC" "$DEST"; then
    log "$DEST already matches $SRC — nothing to do"
else
    if [ -f "$DEST" ]; then
        backup="${DEST}.bak-$(date +%Y%m%d-%H%M%S)"
        cp "$DEST" "$backup"
        log "backed up existing copy to $backup"
    fi
    install -m 755 "$SRC" "$DEST"
    log "installed $SRC → $DEST"
fi

# Report current cron state (user crontab only — don't touch root).
if crontab -l >/dev/null 2>&1; then
    if crontab -l 2>/dev/null | grep -qE '^\s*\*/5 \* \* \* \* .*mission-control-watchdog'; then
        log "cron: active (uncommented line present)"
    elif crontab -l 2>/dev/null | grep -qE '^\s*#.*mission-control-watchdog'; then
        log "cron: PAUSED (commented line present)"
        if $ENABLE_CRON; then
            tmp=$(mktemp)
            crontab -l 2>/dev/null \
                | sed -E 's|^\s*#(PAUSED-[0-9-]+ )?\s*(\*/5 \* \* \* \* .*mission-control-watchdog.*)$|\2|' \
                > "$tmp"
            crontab "$tmp"
            rm -f "$tmp"
            log "cron: re-enabled"
        else
            log "cron: pass --enable-cron to un-pause"
        fi
    else
        log "cron: no mission-control-watchdog entry found"
    fi
else
    log "cron: no user crontab for $(whoami)"
fi

log "done. Next scheduled run will validate openclaw config before any gateway restart."
