#!/bin/bash
set -uo pipefail
# Emergency: restore critical services to master if heavy is down.
# Designed to run on master when heavy (192.168.0.5) is unreachable.
#
# What gets restored (with stale data):
#   - Gateway (from /mnt/external/openclaw)
#   - MongoDB (from /mnt/external/mongodb)
#   - Mission Control (from /mnt/external/mission-control)
#   - Monitoring services (router-api, stats-collector, watchdog)
#
# What is NOT restored (lives only on heavy):
#   - n8n (prod+staging) — gateway will lack n8n integration
#
# All node services are re-pointed to master's gateway.
# Cloudflare tunnel is updated to serve MC from master.
#
# Usage: bash scripts/emergency-restore-master.sh

set -euo pipefail

LOG="/var/log/emergency-restore.log"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/.env.cluster" 2>/dev/null || true

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $1" | tee -a "$LOG"; }

send_telegram() {
    local msg="$1"
    curl -sf -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d "chat_id=${TELEGRAM_CHAT_ID}&text=$msg" >/dev/null 2>&1 || true
}

log "=== Emergency restore to master ==="
send_telegram "🔄 Emergency restore STARTING on master..."

# 1. Gateway
log "Starting gateway..."
cd /mnt/external/openclaw && docker compose up -d 2>/dev/null || log "WARN: Gateway start failed"
sleep 15

if curl -sf http://127.0.0.1:18789/healthz >/dev/null 2>&1; then
    log "Gateway healthy"
else
    log "WARN: Gateway not healthy yet, continuing..."
fi

# 2. MongoDB (stale fallback — primary data is on heavy)
log "Starting MongoDB (stale fallback)..."
cd /mnt/external/mongodb && docker compose up -d 2>/dev/null || log "WARN: MongoDB start failed"

# 3. Mission Control (stale fallback — primary data is on heavy)
log "Starting Mission Control (stale fallback)..."
cd /mnt/external/mission-control && docker compose up -d 2>/dev/null || log "WARN: Mission Control start failed"

# 4. Re-enable Cloudflare tunnel on master (primary tunnel runs on heavy)
log "Re-enabling Cloudflare tunnel on master..."
sudo sed -i 's|service: http://localhost:5678|service: http://localhost:5678|; s|service: http://localhost:3000|service: http://localhost:3000|' /etc/cloudflared/config.yml 2>/dev/null
sudo systemctl enable --now cloudflared 2>/dev/null || log "WARN: cloudflared start failed"

# 5. Re-point all node services to master gateway
log "Re-pointing node services to master gateway..."
for host in master slave0 slave1; do
    if [ "$host" = "slave0" ]; then
        # slave0 uses Tailscale — needs master's Tailscale IP
        MASTER_TS=$(tailscale status 2>/dev/null | grep "$(hostname)" | awk '{print $1}' || echo "100.88.240.88")
        ssh -o ConnectTimeout=5 "$host" "sudo sed -i 's|--host [0-9.]*|--host $MASTER_TS|' /etc/systemd/system/openclaw-node.service && sudo systemctl daemon-reload" 2>/dev/null || log "WARN: $host node repoint failed"
    else
        ssh -o ConnectTimeout=5 "$host" "sudo sed -i 's|--host [0-9.]*|--host 192.168.0.22|' /etc/systemd/system/openclaw-node.service && sudo systemctl daemon-reload" 2>/dev/null || log "WARN: $host node repoint failed"
    fi
done

# Update local (master) node service too
sudo sed -i 's|--host [0-9.]*|--host 192.168.0.22|' /etc/systemd/system/openclaw-node.service 2>/dev/null
sudo systemctl daemon-reload

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

send_telegram "✅ Emergency restore COMPLETE on master. Running: $RUNNING"
log "=== Emergency restore finished ==="
