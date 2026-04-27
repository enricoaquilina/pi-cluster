#!/bin/bash
set -uo pipefail

LOG="/tmp/nfs-backup-$(date +%Y%m%d).log"

# Pre-rsync: dump databases so rsync picks up fresh copies
mkdir -p /mnt/data/mongodb-dump-latest
rm -rf /mnt/data/mongodb-dump-latest/*
docker exec mongodb mongodump --quiet --gzip \
    --excludeCollection=fs.chunks \
    --out /data/dump-staging/ 2>/dev/null \
    && docker cp mongodb:/data/dump-staging/. /mnt/data/mongodb-dump-latest/ 2>/dev/null \
    && docker exec mongodb rm -rf /data/dump-staging/ 2>/dev/null \
    || logger -t nfs-backup "WARN: mongodump failed"

docker exec mission-control-db pg_dump -U missioncontrol missioncontrol \
    > /mnt/data/mc-dump-latest.sql 2>/dev/null \
    || logger -t nfs-backup "WARN: MC pg_dump failed"

mkdir -p /mnt/data/n8n-backup
docker exec n8n-production n8n export:workflow --all \
    --output=/tmp/n8n-workflows.json 2>/dev/null \
    && docker cp n8n-production:/tmp/n8n-workflows.json /mnt/data/n8n-backup/ 2>/dev/null \
    || logger -t nfs-backup "WARN: n8n workflow export failed"
docker exec n8n-production n8n export:credentials --all \
    --output=/tmp/n8n-credentials.json 2>/dev/null \
    && docker cp n8n-production:/tmp/n8n-credentials.json /mnt/data/n8n-backup/ 2>/dev/null \
    || logger -t nfs-backup "WARN: n8n credential export failed"

/usr/bin/rsync -az --delete --no-group --no-perms \
    --ignore-errors \
    --log-file="$LOG" \
    --exclude='.env' \
    --exclude='*.pyc' \
    --exclude='__pycache__' \
    --exclude='node_modules' \
    --exclude='.git/objects' \
    --exclude='mongodb/data' \
    --exclude='mongodb/silicon_sentiments' \
    --exclude='mongodb/*/venv' \
    --exclude='silicon_sentiments' \
    --exclude='venv' \
    --exclude='docker' \
    --exclude='lost+found' \
    /mnt/data/ master:/mnt/external/ 2>&1
rc=$?

if [ "$rc" -eq 23 ] || [ "$rc" -eq 24 ]; then
    errors=$(grep -c 'failed:' "$LOG" 2>/dev/null || echo '?')
    logger -t nfs-backup "Partial transfer (exit $rc, $errors errors) — see $LOG"
    exit 0
elif [ "$rc" -ne 0 ]; then
    logger -t nfs-backup "Backup failed (exit $rc) — see $LOG"
    exit $rc
fi

ssh master "touch /mnt/external/.last-backup" 2>/dev/null
logger -t nfs-backup "Backup completed successfully"
exit 0
