#!/bin/bash
# Deploy staleness check — runs on heavy, detects if master's auto-deploy
# has stopped running. Alert-only, no auto-recovery.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$SCRIPT_DIR/.env.cluster" ] && source "$SCRIPT_DIR/.env.cluster"
# shellcheck source=scripts/lib/telegram.sh
source "$SCRIPT_DIR/lib/telegram.sh" 2>/dev/null || send_telegram() { :; }

MASTER_HOST="${MASTER_HOST:-master}"
STALE_THRESHOLD="${STALE_THRESHOLD:-1800}"
ALERT_DEDUP_FILE="${XDG_STATE_HOME:-$HOME/.local/state}/watchdog/deploy-staleness-alerted"

mkdir -p "$(dirname "$ALERT_DEDUP_FILE")" 2>/dev/null || true

HEARTBEAT=$(ssh -o ConnectTimeout=5 -o BatchMode=yes "$MASTER_HOST" \
    "cat /tmp/auto-deploy-heartbeat 2>/dev/null" 2>/dev/null) || HEARTBEAT="0"

NOW=$(date +%s)
AGE=$(( NOW - HEARTBEAT ))

if [ "$AGE" -lt "$STALE_THRESHOLD" ]; then
    [ -f "$ALERT_DEDUP_FILE" ] && rm -f "$ALERT_DEDUP_FILE"
    exit 0
fi

if [ -f "$ALERT_DEDUP_FILE" ]; then
    LAST_ALERT_AGE=$(( NOW - $(stat -c %Y "$ALERT_DEDUP_FILE") ))
    [ "$LAST_ALERT_AGE" -lt 3600 ] && exit 0
fi

STALE_MIN=$(( AGE / 60 ))
send_telegram "⚠️ *Deploy Stale*: master auto-deploy hasn't run in ${STALE_MIN}m.
Check master node + auto-deploy timer."
touch "$ALERT_DEDUP_FILE"
