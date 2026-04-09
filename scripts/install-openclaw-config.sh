#!/bin/bash
set -euo pipefail
# install-openclaw-config.sh — render the canonical openclaw.json
# template and install it to the gateway's config path.
#
# Safety properties:
#   1. Renders into a tmpfile first.
#   2. Runs the config validator on the tmpfile. If validation fails,
#      the live config is left untouched.
#   3. Makes a timestamped backup of the existing config before
#      overwriting (unless the rendered output is byte-identical).
#   4. Atomic replace via `mv` within the same filesystem.
#   5. Preserves 0600 perms and enrico:enrico ownership.
#   6. This script does NOT restart the gateway. The running container
#      has the config loaded at startup; a rendered replacement only
#      matters for the next restart. The watchdog's #124 gate will
#      validate it again at that point.
#
# What this script does NOT do (deliberately deferred):
#   - Change the compose bind mount to `:ro`. openclaw may still need
#     write access to the config file for meta.lastTouchedAt updates
#     or doctor-generated migrations. Tracked as a separate follow-up.
#   - Move the canonical install path away from ~/.openclaw/openclaw.json.
#     Keeping the current mount target means this is a zero-risk swap.
#
# Usage:
#   scripts/install-openclaw-config.sh              # render + install
#   scripts/install-openclaw-config.sh --dry-run    # render, validate, diff, exit
#   scripts/install-openclaw-config.sh --env <path>
#   scripts/install-openclaw-config.sh --target <path>

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RENDER="$SCRIPT_DIR/render-openclaw-config.sh"
# Honor OPENCLAW_VALIDATOR so tests can inject a stub. Defaults to the
# sibling script in production, which matches what both the render step
# and the watchdog resolve to.
VALIDATOR="${OPENCLAW_VALIDATOR:-$SCRIPT_DIR/openclaw-config-validate.sh}"

ENV_FILE="${OPENCLAW_CONFIG_ENV_FILE:-$HOME/openclaw/.env}"
TARGET="${OPENCLAW_CONFIG_TARGET:-$HOME/.openclaw/openclaw.json}"
DRY_RUN=false

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=true; shift ;;
        --env) shift; ENV_FILE="${1:-}"; shift ;;
        --target) shift; TARGET="${1:-}"; shift ;;
        -h|--help)
            grep '^#' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
            exit 0
            ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

log() { echo "[install-openclaw-config] $*"; }

if [ ! -x "$RENDER" ]; then
    echo "render script not executable: $RENDER" >&2
    exit 1
fi
if [ ! -x "$VALIDATOR" ]; then
    echo "validator not executable: $VALIDATOR" >&2
    exit 1
fi

# Create the tmp file in the same directory as $TARGET so the final
# `mv` is a rename on the same filesystem — atomic instead of
# copy-and-delete across mounts. If $TARGET's parent dir doesn't
# exist yet (first-ever install), fall back to $TMPDIR; the later
# `mv "$tmp" "$TARGET"` will still work for that first-install case.
target_dir="$(dirname "$TARGET")"
if [ -d "$target_dir" ] && [ -w "$target_dir" ]; then
    tmp="$(mktemp -p "$target_dir" ".openclaw-install.XXXXXX")"
else
    tmp="$(mktemp -t openclaw-install.XXXXXX)"
fi
cleanup() { rm -f "$tmp"; }
trap cleanup EXIT

# 1. Render to tmp. The render script runs the validator internally
# when given -o; propagate its exit code verbatim.
if ! "$RENDER" --env "$ENV_FILE" -o "$tmp" >/dev/null; then
    echo "render failed — live config untouched" >&2
    exit 1
fi
log "rendered to $tmp ($(wc -c < "$tmp") bytes)"

# 2. Diff against the live target to show what would change.
identical=false
if [ -f "$TARGET" ] && [ -r "$TARGET" ]; then
    if cmp -s "$tmp" "$TARGET"; then
        log "rendered output is byte-identical to $TARGET — nothing to do"
        identical=true
    else
        log "rendered output differs from $TARGET:"
        if command -v diff >/dev/null 2>&1; then
            diff -u "$TARGET" "$tmp" 2>&1 | head -40 || true
        fi
    fi
else
    log "$TARGET not present or not readable — will install fresh"
fi

if $DRY_RUN; then
    log "--dry-run: not touching live config"
    # Skip to mount check (same end-of-script path as a real install)
    post_install_skipped_install=true
elif $identical; then
    # No-op install: byte-identical to existing target. Still run
    # the mount check below because compose-drift is independent of
    # whether the rendered bytes changed.
    post_install_skipped_install=true
else
    post_install_skipped_install=false
fi

if ! $post_install_skipped_install; then

# 3. Backup existing live config (if any) with a timestamped name.
if [ -f "$TARGET" ]; then
    backup="${TARGET}.bak-$(date +%Y%m%d-%H%M%S)"
    # Use cat > dest instead of cp so the backup inherits the
    # current user's ownership even if $TARGET is root-owned from
    # a previous incident. If $TARGET isn't readable by the current
    # user, this will fail loudly — which is what we want.
    if ! cat "$TARGET" > "$backup" 2>/dev/null; then
        echo "failed to back up $TARGET to $backup" >&2
        echo "(if $TARGET is root-owned, run: sudo chown $(id -un):$(id -gn) $TARGET)" >&2
        exit 1
    fi
    chmod 600 "$backup"
    log "backed up $TARGET -> $backup"
fi

# 4. Atomic install.
mkdir -p "$(dirname "$TARGET")"
mv "$tmp" "$TARGET"
chmod 600 "$TARGET"
trap - EXIT
log "installed rendered config -> $TARGET"

# 5. Post-install sanity: run the validator against the installed
# file to confirm it's readable and schema-valid from the final path.
if ! "$VALIDATOR" --quiet "$TARGET"; then
    echo "ERROR: post-install validation of $TARGET failed" >&2
    echo "       this should not happen — the render step already validated" >&2
    echo "       the same bytes. Something changed between render and install." >&2
    exit 1
fi
log "post-install validation OK"

fi  # end of "if ! $post_install_skipped_install"

# 6. Post-install mount check: confirm the running gateway has
# openclaw.json mounted read-only. This catches the compose-drift
# scenario where a `git pull` on the upstream openclaw repo blows
# away the local per-file :ro overlay and reverts the mount to
# rw. See configs/openclaw/README.md for the required YAML snippet.
# A warn-only check — the install has already succeeded and this
# doesn't affect the rendered file's correctness.
if command -v docker >/dev/null 2>&1; then
    gateway="${OPENCLAW_GATEWAY_CONTAINER:-openclaw-openclaw-gateway-1}"
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$gateway"; then
        mount_check=$(docker exec -u 0 "$gateway" sh -c \
            'touch /home/node/.openclaw/openclaw.json 2>&1; echo rc=$?' \
            2>/dev/null || true)
        case "$mount_check" in
            *"Read-only file system"*|*"rc=1"*)
                log "mount check: $gateway has openclaw.json :ro (good)"
                ;;
            *"rc=0"*)
                log "WARN: $gateway has openclaw.json MOUNTED RW"
                log "      the 2026-04-07 root:root incident class is not"
                log "      structurally prevented. See pi-cluster/configs/openclaw/"
                log "      README.md for the required compose :ro overlay."
                ;;
            *)
                log "mount check: indeterminate ($mount_check) — skipping"
                ;;
        esac
    else
        log "mount check: $gateway not running — skipped"
    fi
fi

log "done. Gateway container is unaffected until next restart; the"
log "watchdog's #124 gate will validate this file again at that point."
