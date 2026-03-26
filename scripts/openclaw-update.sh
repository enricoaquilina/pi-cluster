#!/bin/bash
# OpenClaw Nightly Auto-Update
# Checks for new OpenClaw versions, backs up, updates, and rebuilds.
# Runs nightly at 4am via cron (after 3am backup).
#
# Config via environment or .env.cluster:
#   OPENCLAW_UPDATE_CHANNEL  — stable|beta|dev (default: stable)
#   OPENCLAW_UPDATE_ALERT    — true|false (default: true)
#   HEAVY_HOST               — hostname of gateway node (default: heavy)
#
# Usage: openclaw-update.sh [--dry-run]

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$SCRIPT_DIR/.env.cluster" ] && source "$SCRIPT_DIR/.env.cluster"

HEAVY_HOST="${HEAVY_HOST:-heavy}"
COMPOSE_DIR="/mnt/external/openclaw"
CHANNEL="${OPENCLAW_UPDATE_CHANNEL:-stable}"
ALERT="${OPENCLAW_UPDATE_ALERT:-true}"
ALERT_SCRIPT="/usr/local/bin/cluster-alert.sh"
export LOG_FILE="/tmp/openclaw-update.log"
DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

# shellcheck source=scripts/lib/log.sh
source "$SCRIPT_DIR/lib/log.sh"
send_alert() { [[ "$ALERT" == "true" ]] && bash "$ALERT_SCRIPT" "$1" 2>/dev/null || true; }

log "=== OpenClaw update check started (channel: $CHANNEL, dry-run: $DRY_RUN) ==="

# Get current version
CURRENT=$(ssh -o ConnectTimeout=5 -o BatchMode=yes "$HEAVY_HOST" \
    "docker exec openclaw-openclaw-gateway-1 openclaw --version 2>/dev/null" | grep -oP '[\d.]+' || echo "unknown")
log "Current version: $CURRENT"

# Get latest version from registry
LATEST=$(ssh -o ConnectTimeout=5 -o BatchMode=yes "$HEAVY_HOST" \
    "docker exec openclaw-openclaw-gateway-1 npm view openclaw version 2>/dev/null" || echo "unknown")
log "Latest version: $LATEST"

if [[ "$CURRENT" == "$LATEST" ]]; then
    log "Already up to date ($CURRENT). No update needed."
    log "=== OpenClaw update check finished ==="
    exit 0
fi

if [[ "$LATEST" == "unknown" || "$CURRENT" == "unknown" ]]; then
    log "ERROR: Could not determine versions (current=$CURRENT, latest=$LATEST)"
    send_alert "OpenClaw update check failed: could not determine versions"
    exit 1
fi

log "Update available: $CURRENT -> $LATEST"

if [[ "$DRY_RUN" == true ]]; then
    log "DRY RUN: Would update from $CURRENT to $LATEST"
    log "=== OpenClaw update check finished (dry-run) ==="
    exit 0
fi

# Back up credentials before update
log "Backing up WhatsApp credentials..."
ssh -o ConnectTimeout=5 -o BatchMode=yes "$HEAVY_HOST" \
    "tar czf /home/enrico/backups/whatsapp-creds-pre-update-$(date +%Y%m%d).tar.gz -C /home/enrico/.openclaw credentials/whatsapp 2>/dev/null" \
    || log "WARN: WhatsApp creds backup failed"

# Pull new base image
log "Pulling openclaw:$LATEST..."
if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$HEAVY_HOST" \
    "docker pull ghcr.io/openclaw/openclaw:$LATEST 2>&1 && docker tag ghcr.io/openclaw/openclaw:$LATEST openclaw:local"; then
    log "ERROR: Failed to pull image for $LATEST"
    send_alert "OpenClaw update FAILED: could not pull image $LATEST"
    exit 1
fi

# Rebuild custom image
log "Rebuilding custom image..."
if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$HEAVY_HOST" \
    "cd $COMPOSE_DIR && docker compose build --no-cache openclaw-gateway 2>&1"; then
    log "ERROR: Docker build failed"
    send_alert "OpenClaw update FAILED: docker build error for $LATEST"
    exit 1
fi

# Restart gateway
log "Restarting gateway..."
if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$HEAVY_HOST" \
    "cd $COMPOSE_DIR && docker compose up -d openclaw-gateway 2>&1"; then
    log "ERROR: Failed to restart gateway"
    send_alert "OpenClaw update FAILED: restart error for $LATEST"
    exit 1
fi

# Wait for health check
log "Waiting for health check..."
sleep 30

NEW_VERSION=$(ssh -o ConnectTimeout=5 -o BatchMode=yes "$HEAVY_HOST" \
    "docker exec openclaw-openclaw-gateway-1 openclaw --version 2>/dev/null" | grep -oP '[\d.]+' || echo "unknown")

if [[ "$NEW_VERSION" == "$LATEST" ]]; then
    log "Update successful: $CURRENT -> $NEW_VERSION"
    send_alert "OpenClaw updated: $CURRENT -> $NEW_VERSION"
else
    log "WARN: Version mismatch after update (expected $LATEST, got $NEW_VERSION)"
    send_alert "OpenClaw update WARNING: expected $LATEST but got $NEW_VERSION"
fi

# Run doctor check
log "Running openclaw doctor..."
ssh -o ConnectTimeout=5 -o BatchMode=yes "$HEAVY_HOST" \
    "docker exec openclaw-openclaw-gateway-1 openclaw doctor 2>&1" | tee -a "$LOG_FILE" || true

log "=== OpenClaw update finished ==="
