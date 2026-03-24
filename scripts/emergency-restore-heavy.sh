#!/bin/bash
# Restore normal operation on heavy after a master emergency.
# Run this on heavy after heavy is back online.
#
# Reverses emergency-restore-master.sh:
#   - Stops gateway/MC/MongoDB on master
#   - Re-points all node services to heavy
#   - Updates Cloudflare tunnel to heavy
#   - Verifies heavy services are running
#
# Usage: bash scripts/emergency-restore-heavy.sh

set -euo pipefail

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $1"; }

log "=== Restoring normal operation to heavy ==="

# 1. Verify heavy services are running
log "Checking heavy services..."
for svc in mission-control-api mission-control-db mongodb n8n-production openclaw-openclaw-gateway-1; do
    if docker ps --format '{{.Names}}' | grep -q "$svc"; then
        log "  $svc: running"
    else
        log "  WARN: $svc not running — start it first"
    fi
done

# 2. Stop emergency services on master
log "Stopping emergency services on master..."
ssh -o ConnectTimeout=5 master "cd /mnt/external/openclaw && docker compose stop 2>/dev/null; cd /mnt/external/mission-control && docker compose stop 2>/dev/null; cd /mnt/external/mongodb && docker compose stop 2>/dev/null" || log "WARN: master cleanup failed"
ssh -o ConnectTimeout=5 master "sudo systemctl disable --now openclaw-router-api openclaw-stats-collector.timer openclaw-watchdog-cluster.timer" 2>/dev/null || true

# 3. Re-point all node services to heavy
log "Re-pointing node services to heavy gateway..."
ssh -o ConnectTimeout=5 master "sudo sed -i 's|--host [0-9.]*|--host 192.168.0.5|' /etc/systemd/system/openclaw-node.service && sudo systemctl daemon-reload && sudo systemctl restart openclaw-node" 2>/dev/null || log "WARN: master node repoint failed"
ssh -o ConnectTimeout=5 slave0 "sudo sed -i 's|--host [0-9.]*|--host 100.85.234.128|' /etc/systemd/system/openclaw-node.service && sudo systemctl daemon-reload && sudo systemctl restart openclaw-node" 2>/dev/null || log "WARN: slave0 node repoint failed"
ssh -o ConnectTimeout=5 slave1 "sudo sed -i 's|--host [0-9.]*|--host 192.168.0.5|' /etc/systemd/system/openclaw-node.service && sudo systemctl daemon-reload && sudo systemctl restart openclaw-node" 2>/dev/null || log "WARN: slave1 node repoint failed"
sudo systemctl restart openclaw-node 2>/dev/null || log "WARN: heavy node restart failed"

# 4. Ensure Cloudflare tunnel is running on heavy, stop emergency tunnel on master
log "Ensuring Cloudflare tunnel on heavy..."
sudo systemctl enable --now cloudflared 2>/dev/null || log "WARN: heavy cloudflared start failed"
ssh -o ConnectTimeout=5 master "sudo systemctl stop cloudflared && sudo systemctl disable cloudflared" 2>/dev/null || log "WARN: master cloudflared stop failed"

# 5. Verify
log "Waiting 15s for nodes to reconnect..."
sleep 15
curl -sf http://127.0.0.1:18789/healthz >/dev/null && log "Gateway: healthy" || log "WARN: Gateway not healthy"
curl -sf http://127.0.0.1:8000/api/nodes >/dev/null && log "MC API: healthy" || log "WARN: MC not healthy"

log "=== Normal operation restored to heavy ==="
