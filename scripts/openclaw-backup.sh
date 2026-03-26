#!/bin/bash
# OpenClaw Cluster Backup
# Backs up all critical cluster state to /mnt/external/backups/
# Runs nightly at 3am via cron. Retains 14 days of backups.
#
# What gets backed up:
#   - Gateway config (openclaw.json)
#   - Device pairing (paired.json + identity files)
#   - Node identities (master + all nodes)
#   - Dispatch log database
#   - Mission Control PostgreSQL dump
#   - Ansible vault secrets
#   - Cluster service config (vars, inventory)
#
# Usage: bash scripts/openclaw-backup.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$SCRIPT_DIR/.env.cluster" ] && source "$SCRIPT_DIR/.env.cluster"

BACKUP_ROOT="/mnt/external/backups"
DATE=$(date +%Y-%m-%d)
BACKUP_DIR="$BACKUP_ROOT/$DATE"
RETENTION_DAYS=14
LOG_FILE="/var/log/openclaw-backup.log"

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $1" | tee -a "$LOG_FILE"; }

log "=== Backup started ==="

mkdir -p "$BACKUP_DIR"/{gateway,identities,mc,ansible,dispatch}

# 1. Gateway config (gateway runs on heavy)
log "Backing up gateway config..."
ssh -o ConnectTimeout=5 -o BatchMode=yes "${HEAVY_HOST:-heavy}" "cat /home/enrico/.openclaw/openclaw.json" \
    > "$BACKUP_DIR/gateway/openclaw.json" 2>/dev/null || log "  WARN: openclaw.json not found on heavy"
ssh -o ConnectTimeout=5 -o BatchMode=yes "${HEAVY_HOST:-heavy}" "sudo cat /home/enrico/.openclaw/devices/paired.json" \
    > "$BACKUP_DIR/gateway/paired.json" 2>/dev/null || log "  WARN: paired.json not found on heavy"
chmod 600 "$BACKUP_DIR/gateway/paired.json" 2>/dev/null || true

# 2. Node identities
log "Backing up identities..."
cp /home/enrico/.openclaw/identity/device.json "$BACKUP_DIR/identities/master-operator.json" 2>/dev/null || true
cp /home/enrico/.openclaw-node/.openclaw/identity/device.json "$BACKUP_DIR/identities/master-node.json" 2>/dev/null || true

for host in slave0 slave1 heavy; do
    ssh -o ConnectTimeout=5 -o BatchMode=yes "$host" \
        "cat /home/enrico/.openclaw/identity/device.json 2>/dev/null || cat /home/enrico/.openclaw-node/.openclaw/identity/device.json 2>/dev/null" \
        > "$BACKUP_DIR/identities/$host.json" 2>/dev/null || log "  WARN: $host identity not found"
done

# 2b. WhatsApp credentials (session keys for linked device)
log "Backing up WhatsApp credentials..."
ssh -o ConnectTimeout=5 -o BatchMode=yes "${HEAVY_HOST:-heavy}" "tar czf - -C /home/enrico/.openclaw credentials/whatsapp" \
    > "$BACKUP_DIR/gateway/whatsapp-creds.tar.gz" 2>/dev/null || log "  WARN: WhatsApp creds not found"

# 3. Dispatch log (lives on heavy)
log "Backing up dispatch log..."
ssh -o ConnectTimeout=5 -o BatchMode=yes "${HEAVY_HOST:-heavy}" "cat ${DISPATCH_LOG_DB:-/home/enrico/data/openclaw-dispatch-log.db}" \
    > "$BACKUP_DIR/dispatch/openclaw-dispatch-log.db" 2>/dev/null || log "  WARN: dispatch log not found on heavy"

# 4. Mission Control database (lives on heavy)
log "Backing up MC database..."
ssh -o ConnectTimeout=5 -o BatchMode=yes "${HEAVY_HOST:-heavy}" \
    "docker exec mission-control-db pg_dump -U missioncontrol missioncontrol" \
    > "$BACKUP_DIR/mc/missioncontrol.sql" 2>/dev/null || log "  WARN: MC dump failed (heavy unreachable?)"

# 5. Ansible vault secrets
log "Backing up Ansible secrets..."
cp /home/enrico/homelab/secrets/*.yml "$BACKUP_DIR/ansible/" 2>/dev/null || true
cp /home/enrico/homelab/vars/*.yml "$BACKUP_DIR/ansible/" 2>/dev/null || true
cp /home/enrico/homelab/inventory/hosts.yml "$BACKUP_DIR/ansible/" 2>/dev/null || true

# 6. Cloudflare tunnel config (lives on heavy)
log "Backing up tunnel config..."
ssh -o ConnectTimeout=5 -o BatchMode=yes "${HEAVY_HOST:-heavy}" "cat /etc/cloudflared/config.yml" \
    > "$BACKUP_DIR/gateway/cloudflared.yml" 2>/dev/null || log "  WARN: cloudflared config not found on heavy"

# Fix ownership so tar works
sudo chown -R enrico:enrico "$BACKUP_DIR" 2>/dev/null

# Compress
log "Compressing..."
tar -czf "$BACKUP_ROOT/backup-$DATE.tar.gz" -C "$BACKUP_ROOT" "$DATE"
rm -rf "$BACKUP_DIR"

# Retention: delete backups older than 14 days
log "Cleaning old backups..."
find "$BACKUP_ROOT" -name "backup-*.tar.gz" -mtime +"$RETENTION_DAYS" -delete 2>/dev/null
BACKUP_COUNT=$(find "$BACKUP_ROOT" -name "backup-*.tar.gz" | wc -l)

# Off-site backup to heavy node
REMOTE_BACKUP_DIR="enrico@${HEAVY_HOST:-heavy}:/home/enrico/backups"
log "Syncing to off-site (heavy)..."
ssh -o ConnectTimeout=5 -o BatchMode=yes "${HEAVY_HOST:-heavy}" "mkdir -p /home/enrico/backups" 2>/dev/null
if rsync -az --timeout=30 "$BACKUP_ROOT/backup-$DATE.tar.gz" "$REMOTE_BACKUP_DIR/" 2>/dev/null; then
    log "Off-site sync complete"
    # Clean old remote backups beyond retention
    ssh -o ConnectTimeout=5 -o BatchMode=yes "${HEAVY_HOST:-heavy}" \
        "find /home/enrico/backups -name 'backup-*.tar.gz' -mtime +$RETENTION_DAYS -delete" 2>/dev/null
else
    log "WARN: Off-site sync to heavy failed"
fi

# Cloud backup (3rd copy — requires rclone configured)
if command -v rclone &>/dev/null && [ -n "${CLOUD_BACKUP_DEST:-}" ]; then
    log "Syncing to cloud ($CLOUD_BACKUP_DEST)..."
    if rclone copy "$BACKUP_ROOT/backup-$DATE.tar.gz" "$CLOUD_BACKUP_DEST" \
        --transfers 1 --bwlimit "${CLOUD_BACKUP_BW_LIMIT:-5M}" 2>/dev/null; then
        log "Cloud sync complete"
        # Clean old cloud backups beyond retention
        rclone delete "$CLOUD_BACKUP_DEST" --min-age "${RETENTION_DAYS}d" 2>/dev/null || true
    else
        log "WARN: Cloud sync failed"
    fi
fi

# Verify
BACKUP_SIZE=$(du -sh "$BACKUP_ROOT/backup-$DATE.tar.gz" 2>/dev/null | cut -f1)
log "Backup complete: $BACKUP_SIZE ($BACKUP_COUNT backups retained)"
log "=== Backup finished ==="
