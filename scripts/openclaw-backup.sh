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

BACKUP_ROOT="/mnt/external/backups"
DATE=$(date +%Y-%m-%d)
BACKUP_DIR="$BACKUP_ROOT/$DATE"
RETENTION_DAYS=14
LOG_FILE="/var/log/openclaw-backup.log"

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $1" | tee -a "$LOG_FILE"; }

log "=== Backup started ==="

mkdir -p "$BACKUP_DIR"/{gateway,identities,mc,ansible,dispatch}

# 1. Gateway config
log "Backing up gateway config..."
cp /home/enrico/.openclaw/openclaw.json "$BACKUP_DIR/gateway/" 2>/dev/null || log "  WARN: openclaw.json not found"
sudo cp /home/enrico/.openclaw/devices/paired.json "$BACKUP_DIR/gateway/" 2>/dev/null || log "  WARN: paired.json not found"
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

# 3. Dispatch log
log "Backing up dispatch log..."
cp /tmp/openclaw-dispatch-log.db "$BACKUP_DIR/dispatch/" 2>/dev/null || log "  WARN: dispatch log not found"

# 4. Mission Control database
log "Backing up MC database..."
docker exec mission-control-db pg_dump -U missioncontrol missioncontrol \
    > "$BACKUP_DIR/mc/missioncontrol.sql" 2>/dev/null || log "  WARN: MC dump failed"

# 5. Ansible vault secrets
log "Backing up Ansible secrets..."
cp /home/enrico/homelab/secrets/*.yml "$BACKUP_DIR/ansible/" 2>/dev/null || true
cp /home/enrico/homelab/vars/*.yml "$BACKUP_DIR/ansible/" 2>/dev/null || true
cp /home/enrico/homelab/inventory/hosts.yml "$BACKUP_DIR/ansible/" 2>/dev/null || true

# 6. Cloudflare tunnel config
log "Backing up tunnel config..."
cp /etc/cloudflared/config.yml "$BACKUP_DIR/gateway/cloudflared.yml" 2>/dev/null || true

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
REMOTE_BACKUP_DIR="enrico@192.168.0.5:/home/enrico/backups"
log "Syncing to off-site (heavy)..."
ssh -o ConnectTimeout=5 -o BatchMode=yes 192.168.0.5 "mkdir -p /home/enrico/backups" 2>/dev/null
if rsync -az --timeout=30 "$BACKUP_ROOT/backup-$DATE.tar.gz" "$REMOTE_BACKUP_DIR/" 2>/dev/null; then
    log "Off-site sync complete"
    # Clean old remote backups beyond retention
    ssh -o ConnectTimeout=5 -o BatchMode=yes 192.168.0.5 \
        "find /home/enrico/backups -name 'backup-*.tar.gz' -mtime +$RETENTION_DAYS -delete" 2>/dev/null
else
    log "WARN: Off-site sync to heavy failed"
fi

# Verify
BACKUP_SIZE=$(du -sh "$BACKUP_ROOT/backup-$DATE.tar.gz" 2>/dev/null | cut -f1)
log "Backup complete: $BACKUP_SIZE ($BACKUP_COUNT backups retained)"
log "=== Backup finished ==="
