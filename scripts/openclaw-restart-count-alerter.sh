#!/bin/bash
# openclaw-restart-count-alerter.sh — watch docker RestartCount deltas
# for the watchdog-managed services and page on flapping.
set -uo pipefail
#
# Why this exists: the mission-control-watchdog restarts unhealthy
# services, but it doesn't itself notice when a service crosses the
# line from "occasional hiccup" into "stuck in a restart loop". On
# 2026-04-09 the openclaw gateway restart-looped for 10+ hours before
# a human noticed. This alerter closes that gap with an independent
# sliding-window count of docker's own RestartCount field.
#
# Design notes:
#   - Uses `docker inspect --format '{{.RestartCount}}' <name>`, which
#     reports the number of times Docker itself has restarted the
#     container under the compose `restart: unless-stopped` policy.
#     `docker compose restart` / `up --force-recreate` do NOT increment
#     this counter (those are explicit operator actions). So this only
#     fires on automatic, repeated crash-and-restart — which is the
#     signal we actually care about.
#   - Sliding window implemented as a per-service TSV file of
#     (unix_ts, count) rows. Rows older than the window are pruned on
#     each run. Delta is computed between the oldest surviving row
#     and the most recent sample.
#   - Alert dedup via a per-service "last_alerted_at" sentinel file.
#     Re-alerts are suppressed until the dedup window expires.
#   - Pure bash + coreutils + docker. jq is NOT required. State is
#     plain text, grep-able, hand-editable for emergencies.
#
# Environment:
#   RESTART_COUNT_SERVICES    space-separated list of container names
#                             (default: openclaw-openclaw-gateway-1
#                              mission-control-api-1 mission-control-proxy-1
#                              mongodb n8n-n8n-1 openclaw-router-api)
#   RESTART_COUNT_WINDOW_SECS sliding window in seconds (default: 1800)
#   RESTART_COUNT_THRESHOLD   delta in window to alert on (default: 3)
#   RESTART_COUNT_DEDUP_SECS  alert dedup window (default: 3600)
#   RESTART_COUNT_STATE_DIR   state dir (default: ${XDG_STATE_HOME:-~/.local/state}/pi-cluster/restart-count)
#   TELEGRAM_BOT_TOKEN        optional, enables Telegram alerts
#   TELEGRAM_CHAT_ID          optional, enables Telegram alerts
#
# Exit codes:
#   0 — ran successfully (may or may not have alerted)
#   3 — invocation failure (docker missing, state dir unwritable, etc.)

STATE_DIR="${RESTART_COUNT_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/pi-cluster/restart-count}"
WINDOW_SECS="${RESTART_COUNT_WINDOW_SECS:-1800}"
THRESHOLD="${RESTART_COUNT_THRESHOLD:-3}"
DEDUP_SECS="${RESTART_COUNT_DEDUP_SECS:-3600}"
SERVICES="${RESTART_COUNT_SERVICES:-openclaw-openclaw-gateway-1 mission-control-api-1 mission-control-proxy-1 mongodb n8n-n8n-1 openclaw-router-api}"

# Dependency injection for tests. In production these are plain
# `docker` and `curl`; in tests, pointed at shim scripts that emit
# deterministic output.
DOCKER_CMD="${RESTART_COUNT_DOCKER_CMD:-docker}"
CURL_CMD="${RESTART_COUNT_CURL_CMD:-curl}"

log() { echo "[restart-count-alerter] $*"; }
log_err() { echo "[restart-count-alerter] $*" >&2; }

if ! command -v "$DOCKER_CMD" >/dev/null 2>&1 && [ ! -x "$DOCKER_CMD" ]; then
    log_err "docker not found: $DOCKER_CMD"
    exit 3
fi

if ! mkdir -p "$STATE_DIR" 2>/dev/null; then
    log_err "cannot create state dir: $STATE_DIR"
    exit 3
fi
if [ ! -w "$STATE_DIR" ]; then
    log_err "state dir not writable: $STATE_DIR"
    exit 3
fi

now=$(date +%s)
cutoff=$((now - WINDOW_SECS))

# Send a deduped Telegram alert. Reuses TELEGRAM_BOT_TOKEN/CHAT_ID from
# the environment (matches the watchdog's conventions so both can share
# the same .env). Dedup is local to this script's state dir, independent
# of the watchdog's dedup cache.
send_alert() {
    local service="$1" message="$2"
    local last_file="$STATE_DIR/$service.last-alerted"
    if [ -f "$last_file" ]; then
        local last
        last=$(cat "$last_file" 2>/dev/null || echo 0)
        if [ "$((now - last))" -lt "$DEDUP_SECS" ]; then
            log "dedup: suppressing alert for $service (last ${last} < ${DEDUP_SECS}s ago)"
            return 0
        fi
    fi
    log "ALERT: $message"
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
        "$CURL_CMD" -sf -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${TELEGRAM_CHAT_ID}" \
            --data-urlencode "text=${message}" \
            -d "parse_mode=Markdown" > /dev/null 2>&1 || true
    fi
    echo "$now" > "$last_file"
}

# Process a single service: sample current count, append, prune,
# compute delta, alert if over threshold.
check_service() {
    local service="$1"
    local state_file="$STATE_DIR/$service.tsv"
    local count

    # docker inspect returns the RestartCount for running + stopped
    # containers. If the container doesn't exist we skip silently —
    # not every host runs every service.
    if ! count=$("$DOCKER_CMD" inspect --format '{{.RestartCount}}' "$service" 2>/dev/null); then
        return 0
    fi
    # Defensive: guarantee count is an integer. A bogus value would
    # poison the state file and never clear.
    case "$count" in
        ''|*[!0-9]*)
            log_err "skip $service: non-integer RestartCount '$count'"
            return 0
            ;;
    esac

    # Append current sample.
    printf '%s\t%s\n' "$now" "$count" >> "$state_file"

    # Prune rows older than the window. Rewrite the file in place
    # (via tmpfile) so a concurrent reader never sees a partial state.
    local tmp="${state_file}.tmp.$$"
    awk -F'\t' -v cutoff="$cutoff" '$1 >= cutoff' "$state_file" > "$tmp"
    mv "$tmp" "$state_file"

    # Need at least 2 samples to compute a delta.
    local sample_count
    sample_count=$(wc -l < "$state_file")
    if [ "$sample_count" -lt 2 ]; then
        return 0
    fi

    # Oldest surviving sample vs the one we just appended.
    local first_count last_count delta window_span
    first_count=$(head -n1 "$state_file" | cut -f2)
    last_count=$(tail -n1 "$state_file" | cut -f2)
    delta=$((last_count - first_count))
    window_span=$(( $(tail -n1 "$state_file" | cut -f1) - $(head -n1 "$state_file" | cut -f1) ))

    # Delta should never be negative in steady state, but a container
    # recreate resets RestartCount to 0. If we see a decrease, treat
    # it as a baseline reset and clear history.
    if [ "$delta" -lt 0 ]; then
        log "$service: RestartCount decreased ($first_count -> $last_count), baseline reset"
        printf '%s\t%s\n' "$now" "$last_count" > "$state_file"
        return 0
    fi

    if [ "$delta" -ge "$THRESHOLD" ]; then
        send_alert "$service" \
            "🖥 Heavy: 🛑 ${service} has restarted ${delta} times in the last $((window_span / 60))m (RestartCount ${first_count} → ${last_count}). Possible restart loop — check 'docker logs ${service}' and watchdog history."
    fi
}

for svc in $SERVICES; do
    check_service "$svc"
done
