#!/bin/bash
# System Smoke Test — checks all services, tracks state, alerts on transitions
# Usage: system-smoke-test.sh          (interactive, color-coded output)
#        system-smoke-test.sh --cron    (cron mode, post to API, alert on changes)
#        system-smoke-test.sh --json    (output JSON results)
set -uo pipefail

MODE="interactive"
[[ "${1:-}" == "--cron" ]] && MODE="cron"
[[ "${1:-}" == "--json" ]] && MODE="json"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$SCRIPT_DIR/.env.cluster" ] && source "$SCRIPT_DIR/.env.cluster"

HEAVY_IP="${HEAVY_IP:-192.168.0.5}"
HEAVY_HOST="${HEAVY_HOST:-heavy}"
MISSION_CONTROL_API="http://${HEAVY_IP}:8000/api"
API_KEY="${MC_API_KEY:-$(grep '^API_KEY=' /mnt/external/mission-control/.env 2>/dev/null | cut -d= -f2)}"
ALERT_SCRIPT="/usr/local/bin/cluster-alert.sh"
STATE_DIR="/var/run/cluster-health/services"
FAIL_COUNT_DIR="/var/run/cluster-health/fail-counts"
RESULTS_FILE="/tmp/smoke-test-latest.json"
LOG_FILE="/tmp/smoke-test.log"
ALERT_INTERVAL=600  # 10 minutes

RESTART_COUNT_FILE="/var/run/cluster-health/restart-count"
CIRCUIT_LOCK_FILE="/var/run/cluster-health/restart-count.lock"
MAX_RESTARTS_PER_HOUR=3

mkdir -p "$STATE_DIR" "$FAIL_COUNT_DIR"

_ssh() { ssh -o ConnectTimeout=3 -o BatchMode=yes "$@"; }
# NOTE: Do NOT use `timeout X _ssh` — timeout uses execvp() which only finds
# external commands, not shell functions (silent exit 127). Use timed_ssh instead.
timed_ssh() { local t="$1"; shift; timeout "$t" ssh -o ConnectTimeout=3 -o BatchMode=yes "$@"; }

# Colors for interactive mode
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; NC='\033[0m'; BOLD='\033[1m'

NOW=$(date -Iseconds)
TIMESTAMP=$(date +%s)
declare -A RESULTS
declare -A RESPONSE_MS
declare -A ERRORS

# ── External Connectivity Meta-Check ──────────────────────────────────────────

HOST_DNS_OK=true
if ! dig +short google.com @8.8.8.8 +time=3 >/dev/null 2>&1; then
    HOST_DNS_OK=false
fi

# ── Check Functions ───────────────────────────────────────────────────────────

check_service() {
    local name="$1" status="$2" error="${3:-}" ms="${4:-}"
    RESULTS[$name]="$status"
    [[ -n "$error" ]] && ERRORS[$name]="$error"
    [[ -n "$ms" ]] && RESPONSE_MS[$name]="$ms"
}

# 1. OpenClaw Gateway — Docker running + HTTP health
check_openclaw_gateway() {
    if ! curl -sf --max-time 3 http://${HEAVY_IP}:18789/healthz >/dev/null 2>&1; then
        check_service "openclaw-gateway" "down" "Gateway unreachable on heavy"
        return
    fi
    local start_ms end_ms ms
    start_ms=$(date +%s%N)
    if curl -sf --max-time 5 http://${HEAVY_IP}:18789/healthz >/dev/null 2>&1; then
        end_ms=$(date +%s%N)
        ms=$(( (end_ms - start_ms) / 1000000 ))
        check_service "openclaw-gateway" "up" "" "$ms"
    else
        check_service "openclaw-gateway" "degraded" "HTTP health check failed"
    fi
}

# 1b. OpenClaw Gateway — container memory pressure (early OOM warning)
check_gateway_memory() {
    local raw pct
    raw=$(timed_ssh 8 ${HEAVY_HOST} "docker stats --no-stream --format '{{.MemPerc}}' openclaw-openclaw-gateway-1" 2>/dev/null)
    if [[ -z "$raw" ]]; then
        check_service "openclaw-gateway-memory" "down" "Cannot read container memory stats"
        return
    fi
    pct=${raw%%%}  # strip trailing %
    pct=${pct%.*}  # truncate to integer
    if [[ "$pct" -gt 90 ]]; then
        check_service "openclaw-gateway-memory" "down" "Container memory at ${pct}% — OOM imminent"
    elif [[ "$pct" -gt 75 ]]; then
        check_service "openclaw-gateway-memory" "degraded" "Container memory at ${pct}%"
    else
        check_service "openclaw-gateway-memory" "up" "" "0"
    fi
}

# 2. OpenClaw Telegram — DNS resolve + log polling check
check_openclaw_telegram() {
    if [[ "$HOST_DNS_OK" == false ]]; then
        check_service "openclaw-telegram" "down" "Host DNS down"
        return
    fi
    # Check if container can resolve Telegram API
    if ! timed_ssh 8 ${HEAVY_HOST} "docker exec openclaw-openclaw-gateway-1 getent hosts api.telegram.org" >/dev/null 2>&1; then
        check_service "openclaw-telegram" "down" "DNS resolution for api.telegram.org failed inside container"
        return
    fi
    # Check recent logs for polling activity (successful getUpdates within last 5 min)
    local recent_logs
    recent_logs=$(timed_ssh 10 ${HEAVY_HOST} "docker logs --since 5m openclaw-openclaw-gateway-1" 2>&1 | tail -50)
    if echo "$recent_logs" | grep -v "409: Conflict" | grep -qi "getUpdates.*failed\|error.*telegram\|ENOTFOUND\|ETIMEDOUT\|telegram.*ECONNREFUSED" 2>/dev/null; then
        check_service "openclaw-telegram" "down" "Telegram polling errors in recent logs"
        return
    fi
    check_service "openclaw-telegram" "up"
}

# 3. OpenClaw WhatsApp — positive signal check (active listening)
check_openclaw_whatsapp() {
    local recent_logs
    recent_logs=$(timed_ssh 10 ${HEAVY_HOST} "docker logs --since 10m openclaw-openclaw-gateway-1" 2>&1 | tail -100)
    # Positive signal: provider is actively listening
    if echo "$recent_logs" | grep -qi "Listening for personal WhatsApp" 2>/dev/null; then
        check_service "openclaw-whatsapp" "up"
        return
    fi
    # Negative signal: auth/connection errors (not stale-socket — that's normal health-monitor)
    if echo "$recent_logs" | grep -qi "whatsapp.*auth.*fail\|whatsapp.*error.*401\|whatsapp.*logged.out" 2>/dev/null; then
        check_service "openclaw-whatsapp" "down" "WhatsApp auth/connection failure"
        return
    fi
    # No signal either way — provider may be between restarts
    check_service "openclaw-whatsapp" "up"
}

# 4. Mission Control API
check_mc_api() {
    local start_ms end_ms ms
    start_ms=$(date +%s%N)
    if _ssh heavy "curl -sf --max-time 3 http://localhost:8000/health" >/dev/null 2>&1; then
        end_ms=$(date +%s%N)
        ms=$(( (end_ms - start_ms) / 1000000 ))
        check_service "mission-control-api" "up" "" "$ms"
    else
        check_service "mission-control-api" "down" "Health endpoint unreachable"
    fi
}

# 5. PostgreSQL
check_postgres() {
    if timed_ssh 8 ${HEAVY_HOST} "docker exec mission-control-db pg_isready -U missioncontrol" >/dev/null 2>&1; then
        check_service "postgresql" "up"
    else
        check_service "postgresql" "down" "pg_isready failed"
    fi
}

# 6. MongoDB
check_mongodb() {
    if timed_ssh 8 ${HEAVY_HOST} 'docker exec mongodb mongosh --quiet --eval "db.runCommand({ping:1}).ok"' >/dev/null 2>&1; then
        check_service "mongodb" "up"
    else
        check_service "mongodb" "down" "mongosh ping failed"
    fi
}

# 7. n8n Production
check_n8n_prod() {
    local start_ms end_ms ms
    start_ms=$(date +%s%N)
    if curl -sf --max-time 5 http://${HEAVY_IP}:5678/healthz >/dev/null 2>&1; then
        end_ms=$(date +%s%N)
        ms=$(( (end_ms - start_ms) / 1000000 ))
        check_service "n8n-production" "up" "" "$ms"
    else
        check_service "n8n-production" "down" "Health endpoint unreachable"
    fi
}

# 8. n8n Staging (removed — consolidated to single instance)

# 9-10b. OpenClaw nodes — fetched once, checked three times
# _fetch_openclaw_node_status must be called before the three check functions below.
# Sentinels: "api_unreachable" = curl failed; "parse_error" = invalid JSON from API.
_OPENCLAW_NODE_STATUS=""
_fetch_openclaw_node_status() {
    [ -n "$_OPENCLAW_NODE_STATUS" ] && return  # idempotent
    local raw parsed
    raw=$(curl -sf --max-time 5 "http://${HEAVY_IP}:8520/nodes" 2>/dev/null)
    if [ -z "$raw" ]; then
        _OPENCLAW_NODE_STATUS="api_unreachable"
        return
    fi
    parsed=$(echo "$raw" | python3 -c "
import json, sys
data = json.load(sys.stdin)
nodes = {n['name']: n.get('connected', False) for n in data.get('nodes', [])}
for name in ['control', 'build', 'light', 'heavy']:
    print(name, 'true' if nodes.get(name) else 'false')
" 2>/dev/null)
    if [ -z "$parsed" ]; then
        _OPENCLAW_NODE_STATUS="parse_error"
    else
        _OPENCLAW_NODE_STATUS="$parsed"
    fi
}

check_openclaw_slave0() {
    case "$_OPENCLAW_NODE_STATUS" in
        api_unreachable) check_service "openclaw-slave0" "down" "Router API unreachable" ;;
        parse_error)     check_service "openclaw-slave0" "down" "Router API returned invalid JSON" ;;
        *) if echo "$_OPENCLAW_NODE_STATUS" | grep -q "^build true"; then
               check_service "openclaw-slave0" "up"
           else
               check_service "openclaw-slave0" "down" "Node not connected to gateway"
           fi ;;
    esac
}
check_openclaw_slave1() {
    case "$_OPENCLAW_NODE_STATUS" in
        api_unreachable) check_service "openclaw-slave1" "down" "Router API unreachable" ;;
        parse_error)     check_service "openclaw-slave1" "down" "Router API returned invalid JSON" ;;
        *) if echo "$_OPENCLAW_NODE_STATUS" | grep -q "^light true"; then
               check_service "openclaw-slave1" "up"
           else
               check_service "openclaw-slave1" "down" "Node not connected to gateway"
           fi ;;
    esac
}
check_openclaw_heavy() {
    case "$_OPENCLAW_NODE_STATUS" in
        api_unreachable) check_service "openclaw-heavy" "down" "Router API unreachable" ;;
        parse_error)     check_service "openclaw-heavy" "down" "Router API returned invalid JSON" ;;
        *) if echo "$_OPENCLAW_NODE_STATUS" | grep -q "^heavy true"; then
               check_service "openclaw-heavy" "up"
           else
               check_service "openclaw-heavy" "down" "Node not connected to gateway"
           fi ;;
    esac
}

# 10c. Router API
check_router_api() {
    if curl -sf --max-time 5 http://${HEAVY_IP}:8520/health >/dev/null 2>&1; then
        check_service "router-api" "up"
    else
        check_service "router-api" "down" "Router API unreachable"
    fi
}

# 10d. Spreadbot (ClawHub skill inside gateway container on heavy)
check_spreadbot() {
    # Skip if gateway container isn't running — check_openclaw_gateway already reports that
    if ! timed_ssh 8 ${HEAVY_HOST} \
        "docker ps --filter 'name=openclaw-openclaw-gateway-1' --format '{{.Names}}' 2>/dev/null | grep -q gateway" \
        2>/dev/null; then
        return
    fi
    local paused_count
    paused_count=$(timed_ssh 8 ${HEAVY_HOST} \
        "docker logs openclaw-openclaw-gateway-1 --since 30m 2>&1 | grep -ci 'health.pause\|consecutive.*cancelled\|paused.*consecutive'" \
        2>/dev/null || echo "0")
    paused_count=$(echo "$paused_count" | tr -d '[:space:]')
    if [ "${paused_count:-0}" -gt 0 ]; then
        check_service "spreadbot" "degraded" "Health pause detected ($paused_count log lines in last 30m)"
    else
        check_service "spreadbot" "up"
    fi
}

# 11. Polymarket Bot
check_polymarket_bot() {
    local is_active=false
    # Check for running copybot process (may run as raw process or systemd)
    if pgrep -f "copybot.bot" >/dev/null 2>&1; then
        is_active=true
    elif systemctl is-active --quiet polymarket-bot 2>/dev/null; then
        is_active=true
    fi
    if [[ "$is_active" != "true" ]]; then
        check_service "polymarket-bot" "down" "No copybot process running"
        return
    fi
    # Check data freshness - control.json should be updated within last hour
    local data_file="/mnt/external/polymarket-bot/data/control.json"
    if [ -f "$data_file" ]; then
        local age_s=$(( $(date +%s) - $(stat -c %Y "$data_file") ))
        if [ "$age_s" -gt 3600 ]; then
            check_service "polymarket-bot" "degraded" "Data stale (${age_s}s old)"
            return
        fi
    fi
    check_service "polymarket-bot" "up"
}

# 12. Pi-hole DNS
check_pihole() {
    if dig +short +time=2 @192.168.0.53 google.com >/dev/null 2>&1; then
        check_service "pihole-dns" "up"
    else
        check_service "pihole-dns" "down" "DNS resolution failed"
    fi
}

# 13. Cloudflare Tunnel
check_cloudflared() {
    # CF Access returns 302 redirect; both 200 and 302 mean tunnel is reachable
    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
        -H "CF-Access-Client-Id: ${CF_ACCESS_CLIENT_ID:-}" \
        -H "CF-Access-Client-Secret: ${CF_ACCESS_CLIENT_SECRET:-}" \
        https://mc.siliconsentiments.work 2>/dev/null)
    if [ "$http_code" = "200" ] || [ "$http_code" = "302" ]; then
        check_service "cloudflared" "up"
    else
        check_service "cloudflared" "down" "Tunnel unreachable (HTTP $http_code)"
    fi
}

# 14. Docker DNS Health — detect broken DNS leaking into containers
check_docker_dns() {
    local test_container
    test_container="mongodb"  # containers run on heavy
    if ! _ssh ${HEAVY_HOST} "docker ps -q" >/dev/null 2>&1; then
        check_service "docker-dns" "down" "No running containers"
        return
    fi
    if timed_ssh 8 ${HEAVY_HOST} "docker exec mongodb getent hosts google.com" >/dev/null 2>&1; then
        check_service "docker-dns" "up"
    else
        check_service "docker-dns" "down" "Container DNS failed (${test_container})"
    fi
}

# 15. Tailscale DNS Guard — verify full DNS chain health
check_tailscale_dns() {
    local ts_active=false
    grep -q "100.100.100.100" /etc/resolv.conf 2>/dev/null && ts_active=true

    if [[ "$ts_active" == true ]]; then
        # Tailscale MagicDNS is active — verify it resolves
        if ! dig +short +time=3 google.com @100.100.100.100 >/dev/null 2>&1; then
            check_service "tailscale-dns" "down" "Tailscale MagicDNS active but not resolving"
            return
        fi
        # Verify Pi-hole VIP is reachable through the chain
        if ! dig +short +time=3 google.com @192.168.0.53 >/dev/null 2>&1; then
            check_service "tailscale-dns" "degraded" "MagicDNS OK but Pi-hole VIP (192.168.0.53) unreachable"
            return
        fi
        check_service "tailscale-dns" "up"
    else
        # No Tailscale DNS — just check system resolver works
        if dig +short +time=3 google.com >/dev/null 2>&1; then
            check_service "tailscale-dns" "up"
        else
            check_service "tailscale-dns" "down" "System DNS not resolving"
        fi
    fi
}

# 16. NFS Workspace Health — check workspace is accessible and correctly owned
check_nfs_workspace() {
    local ws
    if [ "$(hostname)" = "heavy" ]; then
        ws="/mnt/data/openclaw/workspace"
    else
        ws="/mnt/external/openclaw/workspace"
    fi

    if ! timeout 5 stat "$ws" >/dev/null 2>&1; then
        check_service "nfs-workspace" "down" "workspace unresponsive or missing ($ws)"
        return
    fi

    local root_count
    root_count=$(find "$ws" -maxdepth 2 -user root 2>/dev/null | wc -l)
    if [ "${root_count:-0}" -gt 0 ]; then
        check_service "nfs-workspace" "degraded" "${root_count} root-owned files in workspace"
        return
    fi

    check_service "nfs-workspace" "up"
}

# 17. n8n Staging
check_n8n_staging() {
    local start_ms end_ms ms
    start_ms=$(date +%s%N)
    if curl -sf --max-time 5 http://${HEAVY_IP}:5679/healthz >/dev/null 2>&1; then
        end_ms=$(date +%s%N)
        ms=$(( (end_ms - start_ms) / 1000000 ))
        check_service "n8n-staging" "up" "" "$ms"
    else
        check_service "n8n-staging" "down" "Health endpoint unreachable"
    fi
}

# 18. NFS Backup Timer — verifies the 6h rsync to master is active and recent
check_nfs_backup() {
    if ! systemctl is-active --quiet nfs-backup.timer 2>/dev/null; then
        check_service "nfs-backup" "down" "Timer not active"
        return
    fi
    local last_run
    last_run=$(systemctl show nfs-backup.service --property=ExecMainExitTimestampMonotonic --value 2>/dev/null)
    if [[ -z "$last_run" || "$last_run" == "0" ]]; then
        check_service "nfs-backup" "up"
        return
    fi
    local last_exit_code
    last_exit_code=$(systemctl show nfs-backup.service --property=ExecMainStatus --value 2>/dev/null)
    if [[ "$last_exit_code" != "0" ]]; then
        check_service "nfs-backup" "degraded" "Last run exited with code $last_exit_code"
        return
    fi
    check_service "nfs-backup" "up"
}

# 19. OpenClaw Master Node — check control/orchestrator is connected
check_openclaw_master() {
    case "$_OPENCLAW_NODE_STATUS" in
        api_unreachable) check_service "openclaw-master" "down" "Router API unreachable" ;;
        parse_error)     check_service "openclaw-master" "down" "Router API returned invalid JSON" ;;
        *) if echo "$_OPENCLAW_NODE_STATUS" | grep -q "^control true"; then
               check_service "openclaw-master" "up"
           else
               check_service "openclaw-master" "down" "Node not connected to gateway"
           fi ;;
    esac
}

# 20. Keepalived — Pi-hole HA VIP on slave0 + slave1
check_keepalived() {
    local s0_active s1_active
    s0_active=$(timed_ssh 5 slave0 "systemctl is-active keepalived" 2>/dev/null || echo "inactive")
    s1_active=$(timed_ssh 5 slave1 "systemctl is-active keepalived" 2>/dev/null || echo "inactive")
    if [[ "$s0_active" == "active" && "$s1_active" == "active" ]]; then
        check_service "keepalived" "up"
    elif [[ "$s0_active" == "active" || "$s1_active" == "active" ]]; then
        local down_node="slave0"
        [[ "$s0_active" == "active" ]] && down_node="slave1"
        check_service "keepalived" "degraded" "$down_node keepalived not running"
    else
        check_service "keepalived" "down" "Both nodes keepalived down"
    fi
}

# 21. NVMe Health — wear level, temperature, write rate, integrity errors
check_nvme_health() {
    local smart_out
    smart_out=$(sudo smartctl -A /dev/nvme0 2>/dev/null)
    if [[ -z "$smart_out" ]]; then
        check_service "nvme-health" "down" "Cannot read SMART data"
        return
    fi

    local pct_used temp_c avail_spare media_errors
    pct_used=$(echo "$smart_out" | awk '/Percentage Used:/ {print $3}' | tr -d '%')
    temp_c=$(echo "$smart_out" | awk '/^Temperature:/ {print $2}')
    avail_spare=$(echo "$smart_out" | awk '/Available Spare:/ {gsub(/%/,""); print $3}')
    media_errors=$(echo "$smart_out" | awk '/Media and Data Integrity Errors:/ {print $NF}')

    local issues=()

    if [[ -n "$media_errors" && "$media_errors" -gt 0 ]]; then
        issues+=("${media_errors} media errors")
    fi

    if [[ -n "$pct_used" && "$pct_used" -ge 80 ]]; then
        issues+=("${pct_used}% used")
    fi

    if [[ -n "$temp_c" && "$temp_c" -ge 70 ]]; then
        issues+=("${temp_c}°C")
    fi

    if [[ -n "$avail_spare" && "$avail_spare" -le 10 ]]; then
        issues+=("spare ${avail_spare}%")
    fi

    # Check daily write rate from the wear log (written by disk-health-monitor)
    local wear_log="/var/log/nvme-wear.log"
    if [[ -f "$wear_log" ]]; then
        local last_delta_gb
        last_delta_gb=$(tail -1 "$wear_log" 2>/dev/null | awk -F',' '{print $4}' | tr -d ' ')
        if [[ -n "$last_delta_gb" ]] && awk "BEGIN{exit !($last_delta_gb > 20)}" 2>/dev/null; then
            issues+=("${last_delta_gb}GB written today")
        fi
    fi

    if [[ ${#issues[@]} -gt 0 ]]; then
        local severity="degraded"
        # Escalate to down for media errors or critical wear
        [[ -n "$media_errors" && "$media_errors" -gt 0 ]] && severity="down"
        [[ -n "$pct_used" && "$pct_used" -ge 90 ]] && severity="down"
        [[ -n "$temp_c" && "$temp_c" -ge 85 ]] && severity="down"
        check_service "nvme-health" "$severity" "$(IFS=', '; echo "${issues[*]}")"
    else
        check_service "nvme-health" "up"
    fi
}

# 22. NVMe Write Rate — real-time write sampling to detect runaway I/O
check_nvme_write_rate() {
    local disk_name="nvme0n1"
    local sectors_before sectors_after sectors_written mb_per_sec

    sectors_before=$(awk -v dev="$disk_name" '$3==dev {print $10}' /proc/diskstats 2>/dev/null || echo 0)
    sleep 3
    sectors_after=$(awk -v dev="$disk_name" '$3==dev {print $10}' /proc/diskstats 2>/dev/null || echo 0)

    sectors_written=$(( sectors_after - sectors_before ))
    mb_per_sec=$(( sectors_written * 512 / 3 / 1048576 ))

    if [[ "$mb_per_sec" -gt 50 ]]; then
        check_service "nvme-write-rate" "down" "${mb_per_sec}MB/s sustained writes"
    elif [[ "$mb_per_sec" -gt 20 ]]; then
        check_service "nvme-write-rate" "degraded" "${mb_per_sec}MB/s writes"
    else
        check_service "nvme-write-rate" "up"
    fi
}

check_life_sync() {
    local life_dir="$HOME/life"
    if [ ! -d "$life_dir/.git" ]; then
        check_service "life-sync" "down" "$HOME/life is not a git repo"
        return
    fi
    # Check last commit age < 48h
    local last_commit_ts
    last_commit_ts=$(git -C "$life_dir" log -1 --format='%ct' 2>/dev/null || echo 0)
    local now_ts
    now_ts=$(date +%s)
    local age_hours=$(( (now_ts - last_commit_ts) / 3600 ))
    if [ "$age_hours" -gt 48 ]; then
        check_service "life-sync" "degraded" "last commit ${age_hours}h ago (>48h)"
        return
    fi
    # Check git fsck
    if ! git -C "$life_dir" fsck --no-dangling 2>/dev/null; then
        check_service "life-sync" "degraded" "git fsck failed"
        return
    fi
    check_service "life-sync" "up"
}

# ── Run All Checks ────────────────────────────────────────────────────────────

check_openclaw_gateway
check_gateway_memory
check_openclaw_telegram
check_openclaw_whatsapp
check_mc_api
check_postgres
check_mongodb
check_n8n_prod
check_n8n_staging
_fetch_openclaw_node_status
check_openclaw_master
check_openclaw_slave0
check_openclaw_slave1
check_openclaw_heavy
check_router_api
check_spreadbot
check_polymarket_bot
check_pihole
check_keepalived
check_cloudflared
check_docker_dns
check_tailscale_dns
check_nfs_workspace
check_nfs_backup
check_nvme_health
check_nvme_write_rate
check_life_sync

# ── Output: Interactive Mode ──────────────────────────────────────────────────

if [[ "$MODE" == "interactive" ]]; then
    echo ""
    printf "${BOLD}%-25s %-10s %-8s %s${NC}\n" "SERVICE" "STATUS" "MS" "ERROR"
    echo "────────────────────────────────────────────────────────────────"
    for svc in openclaw-gateway openclaw-gateway-memory openclaw-telegram openclaw-whatsapp mission-control-api postgresql mongodb n8n-production n8n-staging openclaw-master openclaw-slave0 openclaw-slave1 openclaw-heavy router-api spreadbot polymarket-bot pihole-dns keepalived cloudflared docker-dns tailscale-dns nfs-workspace nfs-backup nvme-health nvme-write-rate life-sync; do
        status="${RESULTS[$svc]:-unknown}"
        ms="${RESPONSE_MS[$svc]:-}"
        err="${ERRORS[$svc]:-}"
        case "$status" in
            up)       color="$GREEN"; icon="PASS" ;;
            degraded) color="$YELLOW"; icon="WARN" ;;
            down)     color="$RED"; icon="FAIL" ;;
            *)        color="$NC"; icon="????" ;;
        esac
        printf "${color}%-25s %-10s %-8s %s${NC}\n" "$svc" "$icon" "${ms:+${ms}ms}" "$err"
    done
    [[ "$HOST_DNS_OK" == false ]] && printf "\n${RED}⚠ Host DNS is down — external checks may be unreliable${NC}\n"
    echo ""
    exit 0
fi

# ── Output: JSON Mode ────────────────────────────────────────────────────────

build_json() {
    local json='{"timestamp":"'"$NOW"'","host_dns_ok":'"$HOST_DNS_OK"',"services":['
    local first=true
    for svc in "${!RESULTS[@]}"; do
        [[ "$first" == true ]] && first=false || json+=","
        json+='{"service":"'"$svc"'","status":"'"${RESULTS[$svc]}"'"'
        [[ -n "${RESPONSE_MS[$svc]:-}" ]] && json+=',"response_ms":'"${RESPONSE_MS[$svc]}"
        [[ -n "${ERRORS[$svc]:-}" ]] && json+=',"error":"'"$(echo "${ERRORS[$svc]}" | sed 's/"/\\"/g')"'"'
        json+='}'
    done
    json+=']}'
    echo "$json"
}

if [[ "$MODE" == "json" ]]; then
    build_json | tee "$RESULTS_FILE"
    exit 0
fi

# ── Cron Mode: State Tracking + Alerts + API Post ────────────────────────────

# Critical services that trigger immediate alerts
# shellcheck disable=SC2034  # used for reference/future alerting tiers
declare -A CRITICAL=(
    [openclaw-gateway]=1 [openclaw-gateway-memory]=1 [openclaw-telegram]=1 [openclaw-whatsapp]=1
    [mission-control-api]=1 [postgresql]=1 [openclaw-slave0]=1
    [polymarket-bot]=1 [pihole-dns]=1
)

send_alert() {
    local msg="$1"
    bash "$ALERT_SCRIPT" "$msg" 2>/dev/null || true
}

# Circuit breaker: cap auto-recovery restarts per hour (flock prevents TOCTOU race)
check_circuit_breaker() {
    (
        flock -x 200
        if [ -f "$RESTART_COUNT_FILE" ]; then
            AGE=$(( $(date +%s) - $(stat -c %Y "$RESTART_COUNT_FILE") ))
            [ "$AGE" -gt 3600 ] && echo 0 > "$RESTART_COUNT_FILE"
        fi
        COUNT=$(cat "$RESTART_COUNT_FILE" 2>/dev/null || echo 0)
        if [ "$COUNT" -ge "$MAX_RESTARTS_PER_HOUR" ]; then
            send_alert "Circuit breaker: $COUNT restarts this hour, skipping auto-recovery"
            exit 1
        fi
        echo $((COUNT + 1)) > "$RESTART_COUNT_FILE"
        exit 0
    ) 200>"$CIRCUIT_LOCK_FILE"
    return $?
}

post_to_api() {
    local json='{"checks":['
    local first=true
    for svc in "${!RESULTS[@]}"; do
        [[ "$first" == true ]] && first=false || json+=","
        json+='{"service":"'"$svc"'","status":"'"${RESULTS[$svc]}"'"'
        [[ -n "${RESPONSE_MS[$svc]:-}" ]] && json+=',"response_ms":'"${RESPONSE_MS[$svc]}"
        [[ -n "${ERRORS[$svc]:-}" ]] && json+=',"error":"'"$(echo "${ERRORS[$svc]}" | sed 's/"/\\"/g')"'"'
        json+=',"checked_at":"'"$NOW"'"}'
    done
    json+=']}'
    curl -sf --max-time 10 -X POST "${MISSION_CONTROL_API}/services/check" \
        -H "x-api-key: ${API_KEY}" \
        -H "Content-Type: application/json" \
        -d "$json" >/dev/null 2>&1 || true
}

post_alert_to_api() {
    local svc="$1" status="$2" msg="$3" downtime="${4:-}"
    local json
    json='{"service":"'"$svc"'","status":"'"$status"'","message":"'"$(echo "$msg" | sed 's/"/\\"/g')"'"'
    [[ -n "$downtime" ]] && json+=',"downtime_seconds":'"$downtime"
    json+='}'
    curl -sf --max-time 10 -X POST "${MISSION_CONTROL_API}/services/alert" \
        -H "x-api-key: ${API_KEY}" \
        -H "Content-Type: application/json" \
        -d "$json" >/dev/null 2>&1 || true
}

for svc in "${!RESULTS[@]}"; do
    new_status="${RESULTS[$svc]}"
    status_file="$STATE_DIR/${svc}.status"
    since_file="$STATE_DIR/${svc}.since"
    notified_file="$STATE_DIR/${svc}.notified"

    prev_status="up"
    [[ -f "$status_file" ]] && prev_status=$(cat "$status_file")

    # State change detection
    if [[ "$new_status" != "$prev_status" ]]; then
        echo "$new_status" > "$status_file"
        echo "$TIMESTAMP" > "$since_file"
        echo "$TIMESTAMP" > "$notified_file"

        error_info="${ERRORS[$svc]:-}"

        if [[ "$new_status" == "down" ]]; then
            msg="SERVICE DOWN: ${svc}"
            [[ -n "$error_info" ]] && msg+="\nCheck: ${error_info}"
            send_alert "$msg"
            post_alert_to_api "$svc" "down" "$msg"
            # Reset fail count
            echo "1" > "$FAIL_COUNT_DIR/${svc}.count"
        elif [[ "$new_status" == "up" && "$prev_status" == "down" ]]; then
            since_ts=$(cat "$since_file" 2>/dev/null || echo "$TIMESTAMP")
            downtime=$(( TIMESTAMP - since_ts ))
            downtime_min=$(( downtime / 60 ))
            msg="SERVICE RECOVERED: ${svc}\nDowntime: ${downtime_min} minutes"
            send_alert "$msg"
            post_alert_to_api "$svc" "recovered" "$msg" "$downtime"
            rm -f "$FAIL_COUNT_DIR/${svc}.count"
        elif [[ "$new_status" == "degraded" ]]; then
            msg="SERVICE DEGRADED: ${svc}"
            [[ -n "$error_info" ]] && msg+="\nCheck: ${error_info}"
            send_alert "$msg"
            post_alert_to_api "$svc" "degraded" "$msg"
        elif [[ "$new_status" == "up" && "$prev_status" == "degraded" ]]; then
            msg="SERVICE RECOVERED: ${svc} (was degraded)"
            send_alert "$msg"
            post_alert_to_api "$svc" "recovered" "$msg"
        fi
    else
        # Same state — check for still-down reminders
        if [[ "$new_status" == "down" ]]; then
            last_notified=$(cat "$notified_file" 2>/dev/null || echo "0")
            elapsed=$(( TIMESTAMP - last_notified ))
            if [[ "$elapsed" -ge "$ALERT_INTERVAL" ]]; then
                since_ts=$(cat "$since_file" 2>/dev/null || echo "$TIMESTAMP")
                downtime_min=$(( (TIMESTAMP - since_ts) / 60 ))
                msg="STILL DOWN: ${svc} (${downtime_min} min)"
                send_alert "$msg"
                echo "$TIMESTAMP" > "$notified_file"
            fi

            # Track consecutive failures for auto-recovery
            fail_count=$(cat "$FAIL_COUNT_DIR/${svc}.count" 2>/dev/null || echo "0")
            fail_count=$((fail_count + 1))
            echo "$fail_count" > "$FAIL_COUNT_DIR/${svc}.count"
        fi
    fi
done

# ── Auto-Recovery: OpenClaw Gateway ──────────────────────────────────────────

openclaw_tg_fails=$(cat "$FAIL_COUNT_DIR/openclaw-telegram.count" 2>/dev/null || echo "0")
openclaw_gw_fails=$(cat "$FAIL_COUNT_DIR/openclaw-gateway.count" 2>/dev/null || echo "0")

if [[ "$openclaw_tg_fails" -ge 3 ]] || [[ "$openclaw_gw_fails" -ge 3 ]]; then
    if ! check_circuit_breaker; then
        echo "[${NOW}] Circuit breaker tripped — skipping Telegram auto-recovery" >> "$LOG_FILE"
    else
        send_alert "AUTO-RECOVERY: Restarting OpenClaw gateway after ${openclaw_tg_fails} consecutive Telegram failures"
        timed_ssh 15 ${HEAVY_HOST} "cd /mnt/external/openclaw && docker compose restart openclaw-gateway" 2>/dev/null
        sleep 30
        # Re-check
        if timed_ssh 8 ${HEAVY_HOST} "docker exec openclaw-openclaw-gateway-1 getent hosts api.telegram.org" >/dev/null 2>&1; then
            send_alert "AUTO-RECOVERY SUCCESS: OpenClaw gateway restarted, Telegram DNS resolving"
            echo "0" > "$FAIL_COUNT_DIR/openclaw-telegram.count"
            echo "0" > "$FAIL_COUNT_DIR/openclaw-gateway.count"
            echo "up" > "$STATE_DIR/openclaw-telegram.status"
            echo "up" > "$STATE_DIR/openclaw-gateway.status"
            # Re-pair nodes only if disconnected (gateway restart invalidates session tokens)
            sleep 20
            _nodes_raw=$(curl -sf --max-time 5 "http://${HEAVY_IP}:8520/nodes" 2>/dev/null)
            _disconnected=$(echo "$_nodes_raw" | python3 -c "
import json,sys
data=json.load(sys.stdin)
bad=[n['name'] for n in data.get('nodes',[]) if not n.get('connected',False) and n['name'] in ('build','light','heavy')]
print(' '.join(bad))
" 2>/dev/null)
            if [[ -n "$_disconnected" ]]; then
                send_alert "AUTO-RECOVERY: Nodes disconnected after gateway restart (${_disconnected}) — re-pairing"
                timed_ssh 60 ${HEAVY_HOST} "bash /home/enrico/pi-cluster/scripts/openclaw-pair-nodes.sh" >> "$LOG_FILE" 2>&1 || true
            fi
        else
            send_alert "AUTO-RECOVERY FAILED: OpenClaw gateway still can't resolve Telegram API after restart"
        fi
    fi
fi

# ── Auto-Recovery: Gateway OOM / Memory Pressure ────────────────────────────
gw_mem_fails=$(cat "$FAIL_COUNT_DIR/openclaw-gateway-memory.count" 2>/dev/null || echo "0")

if [[ "$gw_mem_fails" -ge 2 ]]; then
    restart_count=$(timed_ssh 8 ${HEAVY_HOST} "docker inspect --format '{{.RestartCount}}' openclaw-openclaw-gateway-1" 2>/dev/null || echo "0")
    if [[ "$restart_count" -gt 5 ]] && check_circuit_breaker; then
        send_alert "AUTO-RECOVERY: Gateway OOM loop detected (${restart_count} restarts, memory failures: ${gw_mem_fails}) — recreating container"
        timed_ssh 30 ${HEAVY_HOST} "cd /mnt/external/openclaw && docker compose up -d --force-recreate openclaw-gateway" 2>/dev/null
        sleep 45
        if curl -sf --max-time 5 "http://${HEAVY_IP}:18789/healthz" >/dev/null 2>&1; then
            send_alert "AUTO-RECOVERY SUCCESS: Gateway recreated and healthy"
            echo "0" > "$FAIL_COUNT_DIR/openclaw-gateway-memory.count"
            echo "0" > "$FAIL_COUNT_DIR/openclaw-gateway.count"
            echo "up" > "$STATE_DIR/openclaw-gateway-memory.status"
            echo "up" > "$STATE_DIR/openclaw-gateway.status"
            # Re-pair nodes only if disconnected (force-recreate always invalidates session tokens)
            sleep 20
            _nodes_raw=$(curl -sf --max-time 5 "http://${HEAVY_IP}:8520/nodes" 2>/dev/null)
            _disconnected=$(echo "$_nodes_raw" | python3 -c "
import json,sys
data=json.load(sys.stdin)
bad=[n['name'] for n in data.get('nodes',[]) if not n.get('connected',False) and n['name'] in ('build','light','heavy')]
print(' '.join(bad))
" 2>/dev/null)
            if [[ -n "$_disconnected" ]]; then
                send_alert "AUTO-RECOVERY: Nodes disconnected after gateway recreate (${_disconnected}) — re-pairing"
                timed_ssh 60 ${HEAVY_HOST} "bash /home/enrico/pi-cluster/scripts/openclaw-pair-nodes.sh" >> "$LOG_FILE" 2>&1 || true
            fi
        else
            send_alert "AUTO-RECOVERY FAILED: Gateway still unhealthy after recreate — check NODE_OPTIONS and mem_limit"
        fi
    fi
fi

# ── Auto-Recovery: Disconnected Nodes ────────────────────────────────────────
_node_repaired=0  # prevent calling pair-nodes twice if both nodes are down
for node_entry in slave0:build slave1:light; do
    host="${node_entry%%:*}"
    display="${node_entry##*:}"
    fails=$(cat "$FAIL_COUNT_DIR/openclaw-${host}.count" 2>/dev/null || echo "0")
    if [[ "$fails" -ge 3 ]] && check_circuit_breaker; then
        send_alert "AUTO-RECOVERY: Node ${display} (${host}) disconnected for 15+ min — restarting service"
        timed_ssh 10 "$host" "sudo systemctl restart openclaw-node" 2>/dev/null || true
        sleep 15
        # Check reconnection using same JSON pattern as _fetch_openclaw_node_status()
        node_status=$(curl -sf --max-time 5 "http://${HEAVY_IP}:8520/nodes" 2>/dev/null | \
            python3 -c "import json,sys; d=json.load(sys.stdin); nodes={n['name']:n.get('connected',False) for n in d.get('nodes',[])}; print('connected' if nodes.get('$display') else 'disconnected')" 2>/dev/null)
        if [[ "$node_status" == "connected" ]]; then
            send_alert "AUTO-RECOVERY SUCCESS: Node ${display} reconnected after service restart"
            echo "0" > "$FAIL_COUNT_DIR/openclaw-${host}.count"
            echo "up" > "$STATE_DIR/openclaw-${host}.status"
        elif [[ "$_node_repaired" -eq 0 ]]; then
            # Escalate: device_token_mismatch — re-pair needed (run at most once per cycle)
            send_alert "AUTO-RECOVERY: Node ${display} still disconnected — escalating to re-pair"
            timed_ssh 60 ${HEAVY_HOST} "bash /home/enrico/pi-cluster/scripts/openclaw-pair-nodes.sh" >> "$LOG_FILE" 2>&1 || true
            _node_repaired=1
        else
            send_alert "AUTO-RECOVERY: Node ${display} still disconnected — re-pair already ran this cycle"
        fi
    fi
done

# ── Auto-Recovery: DNS ───────────────────────────────────────────────────────

tailscale_dns_fails=$(cat "$FAIL_COUNT_DIR/tailscale-dns.count" 2>/dev/null || echo "0")
docker_dns_fails=$(cat "$FAIL_COUNT_DIR/docker-dns.count" 2>/dev/null || echo "0")

# If Tailscale MagicDNS is failing to resolve, restart tailscaled
if [[ "$tailscale_dns_fails" -ge 3 ]]; then
    if ! check_circuit_breaker; then
        echo "[${NOW}] Circuit breaker tripped — skipping Tailscale auto-recovery" >> "$LOG_FILE"
    else
        send_alert "AUTO-RECOVERY: Tailscale MagicDNS not resolving for 15+ min — restarting tailscaled"
        sudo systemctl restart tailscaled 2>/dev/null || true
        sleep 10
        if dig +short +time=3 google.com @100.100.100.100 >/dev/null 2>&1; then
            send_alert "AUTO-RECOVERY SUCCESS: Tailscale MagicDNS resolving after restart"
            echo "0" > "$FAIL_COUNT_DIR/tailscale-dns.count"
            echo "up" > "$STATE_DIR/tailscale-dns.status"
        else
            send_alert "AUTO-RECOVERY FAILED: Tailscale MagicDNS still not resolving"
        fi
    fi
fi

# If Docker container DNS is broken for 3+ checks (15 min), restart affected containers
if [[ "$docker_dns_fails" -ge 3 ]]; then
    if ! check_circuit_breaker; then
        echo "[${NOW}] Circuit breaker tripped — skipping Docker DNS auto-recovery" >> "$LOG_FILE"
    else
        send_alert "AUTO-RECOVERY: Docker container DNS broken for 15+ min — restarting OpenClaw gateway"
        timed_ssh 15 ${HEAVY_HOST} "cd /mnt/external/openclaw && docker compose restart openclaw-gateway" 2>/dev/null
        sleep 10
        if timed_ssh 5 ${HEAVY_HOST} docker exec openclaw-openclaw-gateway-1 sh -c "getent hosts google.com" >/dev/null 2>&1; then
            send_alert "AUTO-RECOVERY SUCCESS: Docker container DNS restored after gateway restart"
            echo "0" > "$FAIL_COUNT_DIR/docker-dns.count"
            echo "up" > "$STATE_DIR/docker-dns.status"
        else
            send_alert "AUTO-RECOVERY FAILED: Docker container DNS still broken — may need daemon restart"
        fi
    fi
fi

# ── Post Results to API ──────────────────────────────────────────────────────

if ! post_to_api; then
    bash "$ALERT_SCRIPT" "SMOKE TEST: Failed to post results to MC API" 2>/dev/null || true
fi

# Save JSON locally (always — serves as fallback if API post fails)
build_json > "$RESULTS_FILE"

echo "[${NOW}] Smoke test complete — mode=${MODE}" >> "$LOG_FILE"
# This file was already written above - this is just verifying
