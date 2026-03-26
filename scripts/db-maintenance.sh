#!/bin/bash
# Weekly Database Maintenance
# Runs VACUUM/ANALYZE on PostgreSQL and compact on MongoDB.
# Schedule: Sunday 5am via cron (after 3am backup, 4am update)
#
# Usage: db-maintenance.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$SCRIPT_DIR/.env.cluster" ] && source "$SCRIPT_DIR/.env.cluster"

HEAVY_HOST="${HEAVY_HOST:-heavy}"
LOG_FILE="/tmp/db-maintenance.log"

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $1" | tee -a "$LOG_FILE"; }

log "=== Database maintenance started ==="

# PostgreSQL: VACUUM ANALYZE (Mission Control)
log "PostgreSQL: running VACUUM ANALYZE..."
if ssh -o ConnectTimeout=5 -o BatchMode=yes "$HEAVY_HOST" \
    "docker exec mission-control-db psql -U missioncontrol -c 'VACUUM ANALYZE;'" 2>/dev/null; then
    log "PostgreSQL: VACUUM ANALYZE complete"
else
    log "WARN: PostgreSQL VACUUM ANALYZE failed"
fi

# MongoDB: compact sessions collection
log "MongoDB: running compact..."
MONGO_COLLECTIONS=$(ssh -o ConnectTimeout=5 -o BatchMode=yes "$HEAVY_HOST" \
    'docker exec mongodb mongosh --quiet --eval "db.getCollectionNames().join(\",\")"' 2>/dev/null || echo "")
if [ -n "$MONGO_COLLECTIONS" ] && [ "$MONGO_COLLECTIONS" != "[]" ]; then
    for coll in ${MONGO_COLLECTIONS//,/ }; do
        ssh -o ConnectTimeout=5 -o BatchMode=yes "$HEAVY_HOST" \
            "docker exec mongodb mongosh --quiet --eval 'db.runCommand({compact: \"$coll\"})'" 2>/dev/null \
            && log "MongoDB: compacted $coll" \
            || log "WARN: MongoDB compact failed for $coll"
    done
else
    log "MongoDB: no collections to compact"
fi

# Dispatch log: cleanup old entries + VACUUM (SQLite)
DISPATCH_DB="${DISPATCH_LOG_DB:-/home/enrico/data/openclaw-dispatch-log.db}"
log "Dispatch log: cleaning entries older than 30 days..."
if ssh -o ConnectTimeout=5 -o BatchMode=yes "$HEAVY_HOST" \
    "sqlite3 '$DISPATCH_DB' \"DELETE FROM dispatch_log WHERE timestamp < datetime('now', '-30 days');\" && sqlite3 '$DISPATCH_DB' 'VACUUM;'" 2>/dev/null; then
    log "Dispatch log: cleanup complete"
else
    log "WARN: Dispatch log cleanup failed"
fi

log "=== Database maintenance finished ==="
