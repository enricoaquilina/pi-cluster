#!/bin/bash
set -uo pipefail
# Emergency: restore critical services to master if heavy is down.
# Designed to run on master when heavy (192.168.0.5) is unreachable.
#
# Phase 12 topology: heavy is primary NFS server + all Docker services.
# Master has async backup at /mnt/external (synced every 1h from heavy).
#
# What gets restored (from backup data, up to 1h stale):
#   - NFS server (re-enabled on master, exports /mnt/external to cluster)
#   - Gateway (from /mnt/external/openclaw)
#   - MongoDB (from hourly dump at /mnt/external/mongodb-dump-latest/)
#   - Mission Control (from hourly dump at /mnt/external/mc-dump-latest.sql)
#   - n8n (from hourly export at /mnt/external/n8n-backup/)
#   - Monitoring services (router-api, stats-collector, watchdog)
#
# What is NOT restored:
#   - Secrets (.env files) — must be manually copied from heavy backup
#
# All node services are re-pointed to master's gateway.
# Cloudflare tunnel is updated to serve MC from master.
#
# Usage: bash scripts/emergency-restore-master.sh

set -euo pipefail

LOG="/var/log/emergency-restore.log"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/.env.cluster" 2>/dev/null || true
# shellcheck source=scripts/lib/telegram.sh
source "$SCRIPT_DIR/lib/telegram.sh" 2>/dev/null || send_telegram() { :; }

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $1" | tee -a "$LOG"; }

log "=== Emergency restore to master (heavy down) ==="
send_telegram "🔄 Emergency restore STARTING on master (heavy 192.168.0.5 unreachable)..."

# 0. Re-enable NFS server on master (serve backup data to cluster)
log "Re-enabling NFS server on master..."
sudo systemctl enable --now nfs-kernel-server 2>/dev/null || log "WARN: NFS server start failed"
sudo exportfs -ra 2>/dev/null || log "WARN: exportfs failed"

# 1. Gateway
log "Starting gateway..."
cd /mnt/external/openclaw && docker compose up -d 2>/dev/null || log "WARN: Gateway start failed"
sleep 15

if curl -sf http://127.0.0.1:18789/healthz >/dev/null 2>&1; then
    log "Gateway healthy"
else
    log "WARN: Gateway not healthy yet, continuing..."
fi

# 2. MongoDB (restore from hourly dump)
log "Starting MongoDB..."
cd /mnt/external/mongodb && docker compose up -d 2>/dev/null || log "WARN: MongoDB start failed"
sleep 10
if [ -d /mnt/external/mongodb-dump-latest ]; then
    DUMP_AGE=$(( ($(date +%s) - $(stat -c '%Y' /mnt/external/mongodb-dump-latest 2>/dev/null || echo 0)) / 3600 ))
    [ "$DUMP_AGE" -gt 2 ] && log "WARN: MongoDB dump is ${DUMP_AGE}h old"
    docker exec mongodb mongorestore --drop /data/dump/ 2>/dev/null && log "MongoDB restored from hourly dump" || log "WARN: mongorestore failed"
else
    log "WARN: No MongoDB hourly dump found — data may be stale"
fi

# 3. Mission Control (restore from hourly dump)
log "Starting Mission Control..."
cd /mnt/external/mission-control && docker compose up -d 2>/dev/null || log "WARN: Mission Control start failed"
sleep 10
if [ -f /mnt/external/mc-dump-latest.sql ]; then
    DUMP_AGE=$(( ($(date +%s) - $(stat -c '%Y' /mnt/external/mc-dump-latest.sql)) / 3600 ))
    [ "$DUMP_AGE" -gt 2 ] && log "WARN: MC dump is ${DUMP_AGE}h old"
    docker exec -i mission-control-db psql -U missioncontrol missioncontrol < /mnt/external/mc-dump-latest.sql 2>/dev/null && log "MC restored from hourly dump" || log "WARN: MC dump restore failed"
else
    log "WARN: No MC hourly dump found — data may be stale"
fi

# 4. Re-enable Cloudflare tunnel on master (primary tunnel runs on heavy)
log "Re-enabling Cloudflare tunnel on master..."
sudo systemctl enable --now cloudflared 2>/dev/null || log "WARN: cloudflared start failed"

# 5. Re-point all node services to master gateway (Ansible preferred, sed fallback)
log "Re-pointing node services to master gateway..."
if ansible-playbook /home/enrico/homelab/playbooks/openclaw-nodes.yml \
    -e "openclaw_gateway_host=192.168.0.22" \
    --vault-password-file /home/enrico/homelab/secrets-vault-password 2>&1 | while IFS= read -r l; do log "ansible: $l"; done; then
    log "Nodes re-pointed via Ansible"
else
    log "WARN: Ansible repoint failed — falling back to sed"
    for host in master slave0 slave1; do
        if [ "$host" = "slave0" ]; then
            MASTER_TS=$(tailscale status 2>/dev/null | grep "$(hostname)" | awk '{print $1}')
            if [ -z "$MASTER_TS" ]; then
                log "WARN: Could not determine master's Tailscale IP for slave0"
                continue
            fi
            ssh -o ConnectTimeout=5 "$host" "sudo sed -i 's|--host [0-9.]*|--host $MASTER_TS|' /etc/systemd/system/openclaw-node.service && sudo systemctl daemon-reload" 2>/dev/null || log "WARN: $host node repoint failed"
        else
            ssh -o ConnectTimeout=5 "$host" "sudo sed -i 's|--host [0-9.]*|--host 192.168.0.22|' /etc/systemd/system/openclaw-node.service && sudo systemctl daemon-reload" 2>/dev/null || log "WARN: $host node repoint failed"
        fi
    done
    sudo sed -i 's|--host [0-9.]*|--host 192.168.0.22|' /etc/systemd/system/openclaw-node.service
    sudo systemctl daemon-reload
fi

# 6. Re-pair nodes with gateway on master
log "Re-pairing nodes (waiting 10s for gateway to stabilize)..."
sleep 10
bash "$SCRIPT_DIR/openclaw-pair-nodes.sh" 2>/dev/null || log "WARN: Pairing failed — may need manual intervention"

# 7. Start monitoring services
log "Starting monitoring services..."
sudo systemctl enable --now openclaw-router-api 2>/dev/null || log "WARN: Router API not available"
sudo systemctl enable --now openclaw-stats-collector.timer 2>/dev/null || true
sudo systemctl enable --now openclaw-watchdog-cluster.timer 2>/dev/null || true

# 8. Verify
RUNNING=$(docker ps --format '{{.Names}}' | sort | tr '\n' ', ')
log "Running containers: $RUNNING"

send_telegram "✅ Emergency restore COMPLETE on master. NFS re-enabled. Running: $RUNNING"
log "=== Emergency restore finished ==="
log "NOTE: Data may be up to 1h stale. When heavy recovers, run emergency-restore-heavy.sh"
