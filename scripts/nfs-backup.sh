#!/bin/bash
set -uo pipefail

LOG="/tmp/nfs-backup-$(date +%Y%m%d).log"

/usr/bin/rsync -az --delete --no-group --no-perms \
    --ignore-errors \
    --log-file="$LOG" \
    --exclude='.env' \
    --exclude='*.pyc' \
    --exclude='__pycache__' \
    --exclude='node_modules' \
    --exclude='.git/objects' \
    --exclude='mongodb/data' \
    --exclude='mongodb/*/venv' \
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
