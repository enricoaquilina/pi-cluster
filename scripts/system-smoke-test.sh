#!/bin/bash
# System Smoke Test — checks all services, tracks state, alerts on transitions
# Usage: system-smoke-test.sh          (interactive, color-coded output)
#        system-smoke-test.sh --cron    (cron mode, post to API, alert on changes)
#        system-smoke-test.sh --json    (output JSON results)
set -uo pipefail
# shellcheck disable=SC1090

MODE="interactive"
[[ "${1:-}" == "--cron" ]] && MODE="cron"
[[ "${1:-}" == "--json" ]] && MODE="json"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$SCRIPT_DIR/.env.cluster" ] && source "$SCRIPT_DIR/.env.cluster"

# ── Source shared library and check modules ──────────────────────────────────

source "$SCRIPT_DIR/lib/smoke-common.sh"

for f in "$SCRIPT_DIR/smoke-checks/"*.sh; do
    # shellcheck disable=SC1090
    source "$f"
done

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
_fetch_node_staleness
check_node_stats_heavy
check_node_stats_control
check_node_stats_build
check_node_stats_light
_fetch_watchdog_status
check_watchdog_cluster
check_watchdog_ssh
check_orphan_services
check_systemd_layer_audit
_fetch_service_inventory
check_service_inventory
_fetch_version_data
check_version_consistency

# ── Output: Interactive Mode ──────────────────────────────────────────────────

if [[ "$MODE" == "interactive" ]]; then
    echo ""
    printf "${BOLD}%-25s %-10s %-8s %s${NC}\n" "SERVICE" "STATUS" "MS" "ERROR"
    echo "────────────────────────────────────────────────────────────────"
    for svc in openclaw-gateway openclaw-gateway-memory openclaw-telegram openclaw-whatsapp mission-control-api postgresql mongodb n8n-production n8n-staging openclaw-master openclaw-slave0 openclaw-slave1 openclaw-heavy router-api spreadbot polymarket-bot pihole-dns keepalived cloudflared docker-dns tailscale-dns nfs-workspace nfs-backup nvme-health nvme-write-rate life-sync node-stats-heavy node-stats-control node-stats-build node-stats-light watchdog-cluster watchdog-ssh orphan-services systemd-layer-audit service-inventory version-consistency; do
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

# ── State Tracking Loop ─────────────────────────────────────────────────────

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

        # Still-degraded reminders (hourly)
        if [[ "$new_status" == "degraded" ]]; then
            last_notified=$(cat "$notified_file" 2>/dev/null || echo "0")
            elapsed=$(( TIMESTAMP - last_notified ))
            if [[ "$elapsed" -ge 3600 ]]; then
                since_ts=$(cat "$since_file" 2>/dev/null || echo "$TIMESTAMP")
                downtime_min=$(( (TIMESTAMP - since_ts) / 60 ))
                msg="STILL DEGRADED: ${svc} (${downtime_min} min)"
                send_alert "$msg"
                echo "$TIMESTAMP" > "$notified_file"
            fi
        fi
    fi
done

# ── Auto-Recovery ────────────────────────────────────────────────────────────

for f in "$SCRIPT_DIR/smoke-recovery/"*.sh; do
    # shellcheck disable=SC1090
    source "$f"
done

# ── Post Results to API ──────────────────────────────────────────────────────

if ! post_to_api; then
    bash "$ALERT_SCRIPT" "SMOKE TEST: Failed to post results to MC API" 2>/dev/null || true
fi

# Save JSON locally (always — serves as fallback if API post fails)
build_json > "$RESULTS_FILE"

echo "[${NOW}] Smoke test complete — mode=${MODE}" >> "$LOG_FILE"
