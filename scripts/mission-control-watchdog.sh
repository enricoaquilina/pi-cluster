#!/bin/bash
set -uo pipefail
# Service watchdog — checks all critical services on heavy, auto-restarts and alerts.
# Runs every 5 min via cron on heavy.
#
# Checks: gateway token drift, gateway health, MC API, MC proxy, MongoDB, n8n, router API.
# State: reports to Telegram and syslog.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/.env.cluster" 2>/dev/null || true
# shellcheck source=scripts/lib/telegram.sh
source "$SCRIPT_DIR/lib/telegram.sh" 2>/dev/null || send_telegram() { :; }

MC_DIR="${MC_DIR:-/home/enrico/mission-control}"
GW_DIR="${GW_DIR:-/home/enrico/openclaw}"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [watchdog] $*"; }

ISSUES=0

# 0. Token drift check (env mismatch means restart won't fix auth)
if docker inspect openclaw-openclaw-gateway-1 --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null | grep '^OPENCLAW_GATEWAY_TOKEN=' | cut -d= -f2 > /tmp/_gw_token; then
    EXPECTED=$(grep '^OPENCLAW_GATEWAY_TOKEN=' "$GW_DIR/.env" | cut -d= -f2)
    ACTUAL=$(cat /tmp/_gw_token)
    rm -f /tmp/_gw_token
    if [ -n "$EXPECTED" ] && [ "$EXPECTED" != "$ACTUAL" ]; then
        log "Token drift detected — recreating gateway container"
        send_telegram "🖥 Heavy: ⚠️ Gateway token drift — recreating container"
        logger -t watchdog "Gateway token drift — recreating container"
        cd "$GW_DIR" && docker compose up -d openclaw-gateway --force-recreate 2>&1
        ISSUES=$((ISSUES + 1))
    fi
fi

# 1. Gateway
if ! curl -sf --max-time 5 "http://localhost:18789/healthz" > /dev/null 2>&1; then
    log "Gateway health failed — restarting"
    send_telegram "🖥 Heavy: ⚠️ Gateway unhealthy — restarting"
    logger -t watchdog "Gateway unhealthy — restarting"
    cd "$GW_DIR" && docker compose up -d openclaw-gateway 2>&1
    ISSUES=$((ISSUES + 1))
fi

# 2. Mission Control API
if ! curl -sf --max-time 5 "http://192.168.0.5:8000/health" > /dev/null 2>&1; then
    log "MC API failed — restarting"
    send_telegram "🖥 Heavy: ⚠️ MC API unhealthy — restarting"
    logger -t watchdog "MC API unhealthy — restarting"
    cd "$MC_DIR" && docker compose restart api 2>&1
    ISSUES=$((ISSUES + 1))
fi

# 3. Mission Control Proxy
if ! curl -sf --max-time 5 "http://localhost:3000/health" > /dev/null 2>&1; then
    log "MC Proxy failed — restarting"
    send_telegram "🖥 Heavy: ⚠️ MC Proxy unhealthy — restarting"
    logger -t watchdog "MC Proxy unhealthy — restarting"
    cd "$MC_DIR" && docker compose restart proxy 2>&1
    ISSUES=$((ISSUES + 1))
fi

# 4. MongoDB
if ! docker exec mongodb mongosh --quiet --eval 'db.runCommand({ping:1}).ok' > /dev/null 2>&1; then
    log "MongoDB ping failed — restarting"
    send_telegram "🖥 Heavy: ⚠️ MongoDB down — restarting"
    logger -t watchdog "MongoDB down — restarting"
    docker restart mongodb 2>&1
    ISSUES=$((ISSUES + 1))
fi

# 5. n8n Production
if ! curl -sf --max-time 5 "http://localhost:5678/healthz" > /dev/null 2>&1; then
    log "n8n production failed — restarting"
    send_telegram "🖥 Heavy: ⚠️ n8n production unhealthy — restarting"
    logger -t watchdog "n8n production unhealthy — restarting"
    docker restart n8n-production 2>&1
    ISSUES=$((ISSUES + 1))
fi

# 6. Router API
if ! curl -sf --max-time 5 "http://localhost:8520/health" > /dev/null 2>&1; then
    log "Router API failed — restarting"
    send_telegram "🖥 Heavy: ⚠️ Router API down — restarting"
    logger -t watchdog "Router API down — restarting"
    sudo systemctl restart openclaw-router-api 2>&1
    ISSUES=$((ISSUES + 1))
fi

# 7. MC compose services (catch any that crashed)
cd "$MC_DIR"
if ! docker compose ps --status running --format '{{.Name}}' 2>/dev/null | grep -q mission-control-db; then
    log "MC DB not running — starting compose"
    docker compose up -d 2>&1
    ISSUES=$((ISSUES + 1))
fi

if [ "$ISSUES" -eq 0 ]; then
    log "OK — all services healthy"
else
    log "Fixed $ISSUES issue(s)"
fi
