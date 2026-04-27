#!/bin/bash
set -uo pipefail
# Service watchdog — checks all critical services on heavy, auto-restarts and alerts.
# Runs every 5 min via cron on heavy.
#
# Checks: gateway token drift, gateway health, MC API, MC proxy, MongoDB, n8n, router API.
# State: reports to Telegram, syslog, and Mission Control API.
#
# Hardening (2026-04-09, see fix/openclaw-gateway-restart-loop):
#   - Config validation gate: gateway is never restarted if
#     ~/.openclaw/openclaw.json fails schema validation. The watchdog posts a
#     `degraded` status to MC and sends one deduped Telegram alert instead of
#     looping.
#   - Circuit breaker: after 3 consecutive failed gateway recoveries the
#     watchdog stops restarting and escalates.
#   - flock: concurrent runs are refused so 5-min cron ticks cannot interleave.
#   - Plain unhealthy gateway uses `docker compose restart` (non-destructive)
#     instead of `docker compose up -d`.
#   - Token drift is still gated behind the config validator — no
#     --force-recreate on an invalid config.
#   - Telegram alerts are deduped (same {service,reason} within 30 min).
#
# Environment overrides (mostly for tests):
#   WATCHDOG_STATE       path to JSON state file (default: ~/.local/state/watchdog/state.json)
#   WATCHDOG_LOCK_FILE   path to the flock file (default: ~/.local/state/watchdog/lock)
#   WATCHDOG_ALERT_CACHE path to the alert dedup cache (default: ~/.local/state/watchdog/last_alert.json)
#   WATCHDOG_ALERT_DEDUP_SECS dedup window in seconds (default: 1800)
#   WATCHDOG_CIRCUIT_THRESHOLD consecutive failures to open circuit (default: 3)
#   OPENCLAW_CONFIG      path to openclaw.json (default: /home/enrico/.openclaw/openclaw.json)
#   OPENCLAW_VALIDATE_CMD alternative validator executable (default: alongside this script)
#   MC_DIR, GW_DIR, HEAVY_IP, MC_API_URL, MC_API_KEY — as before

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/.env.cluster" 2>/dev/null || true

MC_DIR="${MC_DIR:-/home/enrico/mission-control}"
GW_DIR="${GW_DIR:-/home/enrico/openclaw}"
HEAVY_IP="${HEAVY_IP:-192.168.0.5}"
MC_API_URL="${MC_API_URL:-http://${HEAVY_IP}:8000/api}"
MC_API_KEY="${MC_API_KEY:-$(grep '^API_KEY=' /mnt/external/mission-control/.env 2>/dev/null | cut -d= -f2)}"

OPENCLAW_CONFIG="${OPENCLAW_CONFIG:-/home/enrico/.openclaw/openclaw.json}"
WATCHDOG_STATE_DIR_DEFAULT="${XDG_STATE_HOME:-$HOME/.local/state}/watchdog"
WATCHDOG_STATE="${WATCHDOG_STATE:-$WATCHDOG_STATE_DIR_DEFAULT/state.json}"
WATCHDOG_LOCK_FILE="${WATCHDOG_LOCK_FILE:-$WATCHDOG_STATE_DIR_DEFAULT/lock}"
WATCHDOG_ALERT_CACHE="${WATCHDOG_ALERT_CACHE:-$WATCHDOG_STATE_DIR_DEFAULT/last_alert.json}"
WATCHDOG_ALERT_DEDUP_SECS="${WATCHDOG_ALERT_DEDUP_SECS:-1800}"
WATCHDOG_CIRCUIT_THRESHOLD="${WATCHDOG_CIRCUIT_THRESHOLD:-3}"

mkdir -p "$(dirname "$WATCHDOG_STATE")" 2>/dev/null || true
mkdir -p "$(dirname "$WATCHDOG_LOCK_FILE")" 2>/dev/null || true
mkdir -p "$(dirname "$WATCHDOG_ALERT_CACHE")" 2>/dev/null || true

# Default validator lives alongside this script. Tests / canary may override.
DEFAULT_VALIDATOR="$SCRIPT_DIR/openclaw-config-validate.sh"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [watchdog] $*"; }

# ---------------------------------------------------------------------------
# flock: refuse concurrent runs. If another watchdog is already running,
# exit cleanly so cron doesn't pile on.
# ---------------------------------------------------------------------------
exec 9> "$WATCHDOG_LOCK_FILE"
if ! flock -n 9; then
    log "another watchdog run is in progress; skipping"
    exit 0
fi

# ---------------------------------------------------------------------------
# State file helpers (gateway_consecutive_failures, last_alerts)
# ---------------------------------------------------------------------------
_state_read_num() {
    local key="$1"
    [ -f "$WATCHDOG_STATE" ] || { echo 0; return; }
    # Accept both compact and pretty-printed JSON.
    local val
    val=$(grep -oE "\"$key\"[[:space:]]*:[[:space:]]*[0-9]+" "$WATCHDOG_STATE" 2>/dev/null | grep -oE '[0-9]+$' | tail -1)
    echo "${val:-0}"
}

_state_write_num() {
    local key="$1" val="$2"
    local tmp
    tmp=$(mktemp "${WATCHDOG_STATE}.tmp.XXXXXX" 2>/dev/null) || tmp="${WATCHDOG_STATE}.tmp.$$"
    if [ -f "$WATCHDOG_STATE" ] && grep -q "\"$key\"" "$WATCHDOG_STATE" 2>/dev/null; then
        sed -E "s/(\"$key\"[[:space:]]*:[[:space:]]*)[0-9]+/\1$val/" "$WATCHDOG_STATE" > "$tmp"
    else
        # Start a fresh minimal object.
        printf '{"%s": %s}\n' "$key" "$val" > "$tmp"
    fi
    mv "$tmp" "$WATCHDOG_STATE"
}

_gateway_failures() { _state_read_num gateway_consecutive_failures; }
_gateway_reset_failures() { _state_write_num gateway_consecutive_failures 0; }
_gateway_inc_failures() {
    local cur
    cur=$(_gateway_failures)
    _state_write_num gateway_consecutive_failures $((cur + 1))
}

# ---------------------------------------------------------------------------
# Telegram + MC helpers with dedup
# ---------------------------------------------------------------------------
_alert_is_fresh() {
    # Returns 0 (fresh = should send) if we have not alerted on (svc,reason)
    # within the dedup window, 1 (stale = suppress) otherwise.
    local key="$1" now last
    now=$(date +%s)
    [ -f "$WATCHDOG_ALERT_CACHE" ] || return 0
    last=$(grep -oE "\"$key\"[[:space:]]*:[[:space:]]*[0-9]+" "$WATCHDOG_ALERT_CACHE" 2>/dev/null | grep -oE '[0-9]+$' | tail -1)
    [ -z "$last" ] && return 0
    [ $((now - last)) -ge "$WATCHDOG_ALERT_DEDUP_SECS" ]
}

_alert_touch() {
    local key="$1" now
    now=$(date +%s)
    local tmp
    tmp=$(mktemp "${WATCHDOG_ALERT_CACHE}.tmp.XXXXXX" 2>/dev/null) || tmp="${WATCHDOG_ALERT_CACHE}.tmp.$$"
    if [ -f "$WATCHDOG_ALERT_CACHE" ] && grep -q "\"$key\"" "$WATCHDOG_ALERT_CACHE" 2>/dev/null; then
        sed -E "s/(\"$key\"[[:space:]]*:[[:space:]]*)[0-9]+/\1$now/" "$WATCHDOG_ALERT_CACHE" > "$tmp"
    else
        printf '{"%s": %s}\n' "$key" "$now" > "$tmp"
    fi
    mv "$tmp" "$WATCHDOG_ALERT_CACHE"
}

send_telegram() {
    local msg="${1:-}" dedup_key="${2:-}"
    if [ -n "$dedup_key" ]; then
        if ! _alert_is_fresh "$dedup_key"; then
            return 0
        fi
        _alert_touch "$dedup_key"
    fi
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
        curl -sf -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${TELEGRAM_CHAT_ID}" \
            --data-urlencode "text=${msg}" \
            -d "parse_mode=Markdown" > /dev/null 2>&1 || true
    fi
}

post_mc_status() {
    local svc="$1" status="$2" ms="${3:-}" reason="${4:-}"
    local now checked_at
    now=$(date +%s)
    checked_at=$(date -d "@$now" -Iseconds 2>/dev/null || date -Iseconds)
    local json
    json="{\"checks\":[{\"service\":\"${svc}\",\"status\":\"${status}\""
    [ -n "$ms" ] && json+=",\"response_ms\":${ms}"
    [ -n "$reason" ] && json+=",\"reason\":\"${reason}\""
    json+=",\"checked_at\":\"${checked_at}\"}]}"
    curl -sf --max-time 10 -X POST "${MC_API_URL}/services/check" \
        -H "x-api-key: ${MC_API_KEY}" \
        -H "Content-Type: application/json" \
        -d "$json" >/dev/null 2>&1 || true
}

# ---------------------------------------------------------------------------
# Config validation gate — called before any gateway restart/recreate.
# Returns 0 if the config is valid, non-zero otherwise.
# ---------------------------------------------------------------------------
validate_openclaw_config() {
    local validator="${OPENCLAW_VALIDATE_CMD:-$DEFAULT_VALIDATOR}"
    if [ ! -x "$validator" ]; then
        log "validator not executable: $validator (skipping gate)"
        return 0  # fail-open: don't block restarts if we can't validate
    fi
    "$validator" --quiet "$OPENCLAW_CONFIG"
}

# If the config is bad, log, post degraded to MC, send a deduped alert,
# and return non-zero so the caller skips the restart.
gate_gateway_on_config() {
    if validate_openclaw_config; then
        return 0
    fi
    log "openclaw config invalid — refusing to restart gateway"
    post_mc_status "openclaw-gateway" "degraded" "" "config_invalid"
    send_telegram "🖥 Heavy: 🛑 Openclaw config invalid — gateway NOT restarted (manual fix required)" \
        "openclaw-gateway:config_invalid"
    return 1
}

ISSUES=0
GW_RESTARTED=false
GW_SKIPPED_CONFIG=false

# ---------------------------------------------------------------------------
# 0. Token drift check — gated by config validator; uses compose restart
#    rather than --force-recreate unless drift persists.
# ---------------------------------------------------------------------------
if docker inspect openclaw-openclaw-gateway-1 --format '{{range .Config.Env}}{{println .}}{{end}}' 2>&1 \
    | grep '^OPENCLAW_GATEWAY_TOKEN=' | cut -d= -f2 > /tmp/_gw_token; then
    EXPECTED=$(grep '^OPENCLAW_GATEWAY_TOKEN=' "$GW_DIR/.env" 2>/dev/null | cut -d= -f2)
    ACTUAL=$(cat /tmp/_gw_token)
    rm -f /tmp/_gw_token
    if [ -n "$EXPECTED" ] && [ "$EXPECTED" != "$ACTUAL" ]; then
        log "Token drift detected — validating config before recreate"
        if gate_gateway_on_config; then
            send_telegram "🖥 Heavy: ⚠️ Gateway token drift — recreating container" \
                "openclaw-gateway:token_drift"
            logger -t watchdog "Gateway token drift — recreating container"
            cd "$GW_DIR" && docker compose up -d openclaw-gateway --force-recreate 2>&1
            ISSUES=$((ISSUES + 1))
            GW_RESTARTED=true
        else
            GW_SKIPPED_CONFIG=true
        fi
    fi
fi

# ---------------------------------------------------------------------------
# 1. Gateway health — gated by config validator AND circuit breaker.
# ---------------------------------------------------------------------------
if [ "$GW_SKIPPED_CONFIG" = false ]; then
    if ! curl -sf --max-time 5 "http://localhost:18789/healthz" > /dev/null 2>&1; then
        fail_count=$(_gateway_failures)
        if [ "$fail_count" -ge "$WATCHDOG_CIRCUIT_THRESHOLD" ]; then
            log "Gateway unhealthy but circuit open (consecutive_failures=$fail_count) — refusing further restarts"
            post_mc_status "openclaw-gateway" "down" "" "circuit_open"
            send_telegram "🖥 Heavy: 🛑 ESCALATION — gateway has failed ${fail_count} consecutive recoveries. Watchdog is no longer restarting. Manual intervention required." \
                "openclaw-gateway:circuit_open"
            ISSUES=$((ISSUES + 1))
        elif gate_gateway_on_config; then
            log "Gateway health failed — restarting (consecutive_failures=$fail_count)"
            send_telegram "🖥 Heavy: ⚠️ Gateway unhealthy — restarting" \
                "openclaw-gateway:unhealthy"
            logger -t watchdog "Gateway unhealthy — restarting"
            cd "$GW_DIR" && docker compose restart openclaw-gateway 2>&1
            ISSUES=$((ISSUES + 1))
            GW_RESTARTED=true
        else
            GW_SKIPPED_CONFIG=true
            ISSUES=$((ISSUES + 1))
        fi
    else
        # Healthy this run — reset the circuit breaker.
        if [ "$(_gateway_failures)" != "0" ]; then
            _gateway_reset_failures
        else
            # Still write the file so tests that seed it can observe the reset.
            _state_write_num gateway_consecutive_failures 0
        fi
    fi
fi

# ---------------------------------------------------------------------------
# 2. Mission Control API
# ---------------------------------------------------------------------------
if ! curl -sf --max-time 5 "http://${HEAVY_IP}:8000/health" > /dev/null 2>&1; then
    log "MC API failed — restarting"
    send_telegram "🖥 Heavy: ⚠️ MC API unhealthy — restarting" "mc-api:unhealthy"
    logger -t watchdog "MC API unhealthy — restarting"
    cd "$MC_DIR" && docker compose restart api 2>&1
    ISSUES=$((ISSUES + 1))
fi

# ---------------------------------------------------------------------------
# 3. Mission Control Proxy
# ---------------------------------------------------------------------------
if ! curl -sf --max-time 5 "http://localhost:3000/health" > /dev/null 2>&1; then
    log "MC Proxy failed — restarting"
    send_telegram "🖥 Heavy: ⚠️ MC Proxy unhealthy — restarting" "mc-proxy:unhealthy"
    logger -t watchdog "MC Proxy unhealthy — restarting"
    cd "$MC_DIR" && docker compose restart proxy 2>&1
    ISSUES=$((ISSUES + 1))
fi

# ---------------------------------------------------------------------------
# 4. MongoDB
# ---------------------------------------------------------------------------
if ! docker exec mongodb mongosh --quiet --eval 'db.runCommand({ping:1}).ok' > /dev/null 2>&1; then
    log "MongoDB ping failed — restarting"
    send_telegram "🖥 Heavy: ⚠️ MongoDB down — restarting" "mongodb:down"
    logger -t watchdog "MongoDB down — restarting"
    docker restart mongodb 2>&1
    ISSUES=$((ISSUES + 1))
fi

# ---------------------------------------------------------------------------
# 5. n8n Production
# ---------------------------------------------------------------------------
if ! curl -sf --max-time 5 "http://localhost:5678/healthz" > /dev/null 2>&1; then
    log "n8n production failed — restarting"
    send_telegram "🖥 Heavy: ⚠️ n8n production unhealthy — restarting" "n8n:unhealthy"
    logger -t watchdog "n8n production unhealthy — restarting"
    docker restart n8n-production 2>&1
    ISSUES=$((ISSUES + 1))
fi

# ---------------------------------------------------------------------------
# 6. Router API
# ---------------------------------------------------------------------------
if ! curl -sf --max-time 5 "http://localhost:8520/health" > /dev/null 2>&1; then
    log "Router API failed — restarting"
    send_telegram "🖥 Heavy: ⚠️ Router API down — restarting" "router-api:down"
    logger -t watchdog "Router API down — restarting"
    sudo systemctl restart openclaw-router-api 2>&1
    ISSUES=$((ISSUES + 1))
fi

# ---------------------------------------------------------------------------
# 7. MC compose services (catch any that crashed)
# ---------------------------------------------------------------------------
if [ -d "$MC_DIR" ] && cd "$MC_DIR" 2>&1; then
    if ! docker compose ps --status running --format '{{.Name}}' 2>&1 | grep -q mission-control-db; then
        log "MC DB not running — starting compose"
        docker compose up -d 2>&1
        ISSUES=$((ISSUES + 1))
    fi
fi

if [ "$ISSUES" -eq 0 ]; then
    log "OK — all services healthy"
else
    log "Fixed/flagged $ISSUES issue(s)"
fi

# ---------------------------------------------------------------------------
# 8. After a gateway restart: wait for healthy, update state file, post MC.
# ---------------------------------------------------------------------------
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
        _gateway_reset_failures
        ms=$(curl -sf --max-time 5 -o /dev/null -w '%{time_total}' "http://localhost:18789/healthz" 2>/dev/null | awk '{printf "%d", $1*1000}')
        post_mc_status "openclaw-gateway" "up" "$ms"
    else
        log "Gateway did not recover within 60s (consecutive_failures will increment next run)"
        _gateway_inc_failures
        post_mc_status "openclaw-gateway" "down" "" "recovery_timeout"
    fi
fi
