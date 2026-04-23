#!/bin/bash
# Weekly/daily life maintenance via Claude Code CLI.
# Runs /weekly skill unattended: daily carry-forward + Monday full review.
# Scheduled via systemd user timer (life-weekly.timer).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/life-automation-lib.sh"
source "$SCRIPT_DIR/weekly-maintenance.conf"

# Load Telegram credentials for failure notifications
ENV_CLUSTER="$HOME/pi-cluster/scripts/.env.cluster"
# shellcheck source=/dev/null
[[ -f "$ENV_CLUSTER" ]] && source "$ENV_CLUSTER"

life_init_env
TAG="weekly-maintenance"
log() { life_log "$TAG" "$*"; }

life_check_topology || { log "ERROR: topology check failed"; exit 1; }

if life_check_llm_killswitch; then
    log "LLM kill switch active — skipping (weekly is 100% LLM)"
    exit 0
fi

life_rotate_logs "$TAG"
life_acquire_lock "$LOG_DIR/weekly-maintenance.lock" || { log "Already running — skipping"; exit 0; }
life_require_daily_note || { log "No daily note for $TODAY — skipping"; exit 0; }
life_require_claude_cli || { log "ERROR: claude CLI not found at $CLAUDE_BIN"; exit 1; }

# --- Mode selection ---
if [[ -n "${MODE_OVERRIDE:-}" ]]; then
    MODE="$MODE_OVERRIDE"
elif [[ "$(date +%u)" -eq 1 ]]; then
    MODE="full"
else
    MODE="daily"
fi

# --- Model selection from config ---
if [[ "$MODE" == "full" ]]; then
    MODEL="${WEEKLY_MODEL_FULL:-sonnet}"
else
    MODEL="${WEEKLY_MODEL_DAILY:-sonnet}"
fi

log "Starting weekly maintenance: mode=$MODE model=$MODEL"

# --- Pre-run state for verification ---
PRE_MTIME=$(stat -c %Y "$DAILY_NOTE" 2>/dev/null || echo 0)
PRE_PENDING=""
if [[ -x "$SCRIPT_DIR/weekly_review_data.py" ]]; then
    PRE_PENDING=$(python3 "$SCRIPT_DIR/weekly_review_data.py" --mode "$MODE" 2>/dev/null \
        | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('pending_items',{})))" 2>/dev/null || echo "")
fi

# --- Claude invocation with retry ---
ATTEMPT=0
MAX_ATTEMPTS=$((WEEKLY_MAX_RETRIES + 1))
SUCCESS=false
START_TIME=$(date +%s)

while [[ $ATTEMPT -lt $MAX_ATTEMPTS ]]; do
    ATTEMPT=$((ATTEMPT + 1))
    log "Claude invocation attempt $ATTEMPT/$MAX_ATTEMPTS"

    if timeout "$WEEKLY_CLAUDE_TIMEOUT" "$CLAUDE_BIN" -p "/weekly $MODE" \
        --output-format text \
        --no-session-persistence \
        --permission-mode auto \
        --model "$MODEL" \
        --append-system-prompt "Running unattended from systemd timer. Follow unattended mode rules: skip all interactive prompts, only do automated actions (carry forward, deadline alerts, changelog). Flag everything else for morning review in daily note." \
        >> "$(life_log_file "$TAG")" 2>&1; then
        SUCCESS=true
        break
    fi

    log "Attempt $ATTEMPT failed (exit $?)"
    if [[ $ATTEMPT -lt $MAX_ATTEMPTS ]]; then
        log "Retrying in ${WEEKLY_RETRY_DELAY}s..."
        sleep "$WEEKLY_RETRY_DELAY"
    fi
done

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

if [[ "$SUCCESS" != "true" ]]; then
    log "ERROR: All $MAX_ATTEMPTS attempts failed (${DURATION}s elapsed)"
    if [[ "$WEEKLY_NOTIFY_ON_FAILURE" == "true" ]]; then
        life_notify_telegram "⚠️ *Weekly maintenance failed* — mode=$MODE, $MAX_ATTEMPTS attempts, ${DURATION}s elapsed"
    fi
    exit 0
fi

log "Claude completed in ${DURATION}s"

# --- Post-run verification ---
POST_MTIME=$(stat -c %Y "$DAILY_NOTE" 2>/dev/null || echo 0)

if [[ "$PRE_PENDING" =~ ^[0-9]+$ ]] && [[ "$PRE_PENDING" -gt 0 ]] && [[ "$POST_MTIME" -eq "$PRE_MTIME" ]]; then
    log "WARNING: daily note unchanged despite $PRE_PENDING pending items — carry-forward may have failed"
fi

# --- Idempotency check ---
if [[ -x "$SCRIPT_DIR/weekly_review_data.py" ]] && [[ "$PRE_PENDING" =~ ^[0-9]+$ ]]; then
    POST_PENDING=$(python3 "$SCRIPT_DIR/weekly_review_data.py" --mode "$MODE" 2>/dev/null \
        | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('pending_items',{})))" 2>/dev/null || echo "")
    if [[ "$POST_PENDING" =~ ^[0-9]+$ ]] && [[ "$POST_PENDING" -gt "$PRE_PENDING" ]]; then
        log "WARNING: pending items grew from $PRE_PENDING to $POST_PENDING — possible duplication"
    fi
fi

# --- Heartbeat ---
touch "$LOG_DIR/weekly-maintenance.heartbeat"
log "Weekly maintenance complete: mode=$MODE duration=${DURATION}s"
