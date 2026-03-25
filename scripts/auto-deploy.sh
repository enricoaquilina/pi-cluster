#!/bin/bash
# Auto-deploy: pull new commits and apply Ansible configs
# Runs every 5 min via cron on master. Idempotent — safe to run repeatedly.
set -uo pipefail

REPO_DIR="/home/enrico/homelab"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_TAG="auto-deploy"

[ -f "$SCRIPT_DIR/.env.cluster" ] && source "$SCRIPT_DIR/.env.cluster"

log() { logger -t "$LOG_TAG" "$1"; }

cd "$REPO_DIR" || exit 1

# Fetch latest from origin
git fetch origin master --quiet 2>/dev/null || exit 0

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/master)

# Nothing to do if up to date
[ "$LOCAL" = "$REMOTE" ] && exit 0

# Pull (fast-forward only — never create merge commits)
if ! git pull --ff-only origin master 2>&1 | while read -r line; do log "$line"; done; then
    log "ERROR: pull failed (merge conflict?), skipping deploy"
    exit 1
fi

NEW_HEAD=$(git rev-parse --short HEAD)
COMMITS=$(git log --oneline "$LOCAL..$REMOTE" | head -5)
log "Deployed $LOCAL..$NEW_HEAD"

# Run idempotent Ansible playbooks to apply any config changes
log "Running log-maintenance playbook..."
ansible-playbook "$REPO_DIR/playbooks/log-maintenance.yml" --quiet 2>&1 | while read -r line; do log "log-maint: $line"; done

log "Running openclaw-monitoring playbook..."
ansible-playbook "$REPO_DIR/playbooks/openclaw-monitoring.yml" --quiet 2>&1 | while read -r line; do log "monitoring: $line"; done

log "Deploy complete"

# Telegram notification
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
    curl -sf -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d "chat_id=${TELEGRAM_CHAT_ID}" \
        -d "text=🚀 *Auto-Deploy*
$COMMITS" \
        -d "parse_mode=Markdown" > /dev/null 2>&1 || true
fi
