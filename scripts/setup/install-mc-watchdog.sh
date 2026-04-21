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
# ACTIVATES the PR #124 config-validation gate. The watchdog's
# DEFAULT_VALIDATOR resolves to `$SCRIPT_DIR/openclaw-config-validate.sh`,
# i.e. a sibling of wherever the watchdog lives. Before this installer
# started depositing the validator alongside the watchdog, that path was
# empty on heavy, so `[ ! -x "$validator" ]` in the watchdog flipped the
# gate into a silent no-op: every cron tick logged "validator not
# executable (skipping gate)" and restarts proceeded without any config
# validation. After this installer runs, the validator is on disk and
# the gate actually runs. The gate's behaviour is load-reducing: if the
# config is valid (current state) the gate is a no-op; if it ever goes
# invalid, the watchdog refuses to restart the gateway instead of
# thrashing. See the post-install sanity check below.
#
# Idempotent. Safe to re-run. Exits 0 if nothing needed to change.
#
# Usage:
#   scripts/install-mc-watchdog.sh              # install + print cron status
#   scripts/install-mc-watchdog.sh --enable-cron  # also uncomment a paused line

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$SCRIPT_DIR/mission-control-watchdog.sh"
VALIDATOR_SRC="$SCRIPT_DIR/openclaw-config-validate.sh"
DEST="${MC_WATCHDOG_INSTALL_PATH:-$HOME/.local/bin/mission-control-watchdog}"
# The watchdog resolves its validator via:
#   DEFAULT_VALIDATOR="$SCRIPT_DIR/openclaw-config-validate.sh"
# where $SCRIPT_DIR is dirname of the *installed* watchdog. So deposit
# the validator next to $DEST under the exact basename the watchdog
# computes. Keeping these two paths coupled is what makes the gate
# actually run.
VALIDATOR_DEST="$(dirname "$DEST")/openclaw-config-validate.sh"
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
if [ ! -f "$VALIDATOR_SRC" ]; then
    echo "validator source missing: $VALIDATOR_SRC" >&2
    exit 1
fi

# Syntax-check before overwriting the live copy.
if ! bash -n "$SRC"; then
    echo "source script has bash syntax errors — refusing to install" >&2
    exit 1
fi
if ! bash -n "$VALIDATOR_SRC"; then
    echo "validator source has bash syntax errors — refusing to install" >&2
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

# --- Deposit validator alongside watchdog (activates the #124 gate) ---
validator_was_fresh=false
if [ -f "$VALIDATOR_DEST" ] && cmp -s "$VALIDATOR_SRC" "$VALIDATOR_DEST"; then
    log "$VALIDATOR_DEST already matches $VALIDATOR_SRC — nothing to do"
else
    if [ -f "$VALIDATOR_DEST" ]; then
        backup="${VALIDATOR_DEST}.bak-$(date +%Y%m%d-%H%M%S)"
        cp "$VALIDATOR_DEST" "$backup"
        log "backed up existing validator copy to $backup"
    else
        validator_was_fresh=true
    fi
    install -m 755 "$VALIDATOR_SRC" "$VALIDATOR_DEST"
    log "installed $VALIDATOR_SRC → $VALIDATOR_DEST"
fi

# --- Post-install sanity: watchdog can actually see its validator ---
# $VALIDATOR_DEST is defined at the top of this script as
#   "$(dirname "$DEST")/openclaw-config-validate.sh"
# which is exactly what the watchdog's DEFAULT_VALIDATOR resolves to
# at runtime. Reuse that one source of truth here instead of
# recomputing it — a second local expression would be an internal
# drift risk of the exact class this sanity check exists to prevent.
if [ ! -x "$VALIDATOR_DEST" ]; then
    log "ERROR: post-install sanity check failed"
    log "       watchdog expects an executable validator at:"
    log "         $VALIDATOR_DEST"
    log "       but it is missing or not executable. The #124 gate will"
    log "       silently skip at every cron tick."
    exit 1
fi
if $validator_was_fresh; then
    log "ACTIVATED: PR #124 config-validation gate is now live."
    log "           Before this install, the validator was absent at"
    log "           $VALIDATOR_DEST, so the watchdog was skipping"
    log "           validation and restarting the gateway without any"
    log "           pre-flight config check. Starting with the next"
    log "           cron tick, restarts are gated on 'openclaw doctor'"
    log "           passing against the on-disk config."
else
    log "validator already present — #124 gate was already active"
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
