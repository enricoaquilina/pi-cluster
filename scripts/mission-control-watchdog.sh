#!/bin/bash
set -uo pipefail
# Service watchdog — checks all critical services on heavy, auto-restarts and alerts.
# Runs every 5 min via cron on heavy.
#
# Checks: gateway token drift, gateway health, MC API, MC proxy, MongoDB, n8n, router API.
# State: reports to Telegram, syslog, and Mission Control API.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/.env.cluster" 2>/dev/null || true

MC_DIR="${MC_DIR:-/home/enrico/mission-control}"
GW_DIR="${GW_DIR:-/home/enrico/openclaw}"
HEAVY_IP="${HEAVY_IP:-192.168.0.5}"
MC_API_URL="${MC_API_URL:-http://${HEAVY_IP}:8000/api}"
MC_API_KEY="${MC_API_KEY:-$(grep '^API_KEY=' /mnt/external/mission-control/.env 2>/dev/null | cut -d= -f2)}"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [watchdog] $*"; }

send_telegram() {
    local msg="${1:-}"
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
        curl -sf -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${TELEGRAM_CHAT_ID}" \
            --data-urlencode "text=${msg}" \
            -d "parse_mode=Markdown" > /dev/null 2>&1 || true
    fi
}

post_mc_status() {
    local svc="$1" status="$2" ms="${3:-}"
    local now checked_at
    now=$(date +%s)
    checked_at=$(date -d "@$now" -Iseconds 2>/dev/null || date -Iseconds)
    local json
    json="{\"checks\":[{\"service\":\"${svc}\",\"status\":\"${status}\""
    [ -n "$ms" ] && json+=",\"response_ms\":${ms}"
    json+=",\"checked_at\":\"${checked_at}\"}]}"
    curl -sf --max-time 10 -X POST "${MC_API_URL}/services/check" \
        -H "x-api-key: ${MC_API_KEY}" \
        -H "Content-Type: application/json" \
        -d "$json" >/dev/null 2>&1 || true
}

ISSUES=0
GW_RESTARTED=false

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
        GW_RESTARTED=true
    fi
fi

# 1. Gateway
if ! curl -sf --max-time 5 "http://localhost:18789/healthz" > /dev/null 2>&1; then
    log "Gateway health failed — restarting"
    send_telegram "🖥 Heavy: ⚠️ Gateway unhealthy — restarting"
    logger -t watchdog "Gateway unhealthy — restarting"
    cd "$GW_DIR" && docker compose up -d openclaw-gateway 2>&1
    ISSUES=$((ISSUES + 1))
    GW_RESTARTED=true
fi

# 2. Mission Control API
if ! curl -sf --max-time 5 "http://${HEAVY_IP}:8000/health" > /dev/null 2>&1; then
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
cd "$MC_DIR" || exit
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

# 8. After gateway restart: wait for healthy and post "up" to MC immediately
if [ "$GW_RESTARTED" = true ]; then
    log "Waiting for gateway to stabilize before posting status..."
    gw_ok=false
    for _ in $(seq 1 12); do
        sleep 5
        if curl -sf --max-time 5 "http://localhost:18789/healthz" > /dev/null 2>&1; then
            gw_ok=true
            break
        fi
    done
    if [ "$gw_ok" = true ]; then
        log "Gateway recovered — posting up status to MC"
        ms=$(curl -sf --max-time 5 -o /dev/null -w '%{time_total}' "http://localhost:18789/healthz" 2>/dev/null | awk '{printf "%d", $1*1000}')
        post_mc_status "openclaw-gateway" "up" "$ms"
    else
        log "Gateway did not recover within 60s"
    fi
fi
