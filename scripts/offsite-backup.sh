#!/bin/bash
# Offsite backup to Backblaze B2 — critical data only
# Requires: rclone configured with remote "b2-backup"
# Setup: rclone config → New remote → Backblaze B2 → enter key ID + app key
set -uo pipefail

BUCKET="b2-backup:pi-cluster-backup"
LOG="/tmp/offsite-backup-$(date +%Y%m%d).log"
TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)

if ! command -v rclone &>/dev/null; then
    logger -t offsite-backup "rclone not installed"
    exit 1
fi

if ! rclone lsd "$BUCKET" &>/dev/null; then
    logger -t offsite-backup "Cannot access bucket — check rclone config"
    exit 1
fi

backup_dir() {
    local src="$1" dest="$2"
    rclone sync "$src" "$BUCKET/$dest" \
        --log-file="$LOG" \
        --log-level INFO \
        --transfers 4 \
        --checkers 8 \
        --exclude '.env' \
        --exclude '*.pyc' \
        --exclude '__pycache__/**' \
        --exclude 'node_modules/**' \
        --exclude '.git/objects/**' \
        --exclude 'venv/**' \
        --bwlimit 5M \
        2>&1
}

# MongoDB dump (fresh)
mongodump_dir="/tmp/offsite-mongodump"
rm -rf "$mongodump_dir"
if mongodump --quiet --out="$mongodump_dir" 2>/dev/null; then
    backup_dir "$mongodump_dir" "mongodb/$(date +%Y%m%d)"
    rm -rf "$mongodump_dir"
else
    logger -t offsite-backup "mongodump failed, skipping"
fi

# Life knowledge base
if [ -d "/home/enrico/life" ]; then
    backup_dir "/home/enrico/life" "life"
fi

# Configs and secrets
backup_dir "/home/enrico/pi-cluster/secrets" "configs/vault"
backup_dir "/home/enrico/pi-cluster/playbooks" "configs/playbooks"
backup_dir "/home/enrico/pi-cluster/scripts/.env.cluster" "configs/env-cluster"

# Prune old MongoDB dumps (keep 7 days)
rclone delete "$BUCKET/mongodb" --min-age 7d --log-file="$LOG" 2>/dev/null

logger -t offsite-backup "Offsite backup completed at $TIMESTAMP"
