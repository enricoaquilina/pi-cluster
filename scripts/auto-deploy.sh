#!/bin/bash
# Auto-deploy: pull new commits and apply Ansible configs
# Runs every 5 min via cron on master. Idempotent — safe to run repeatedly.
set -uo pipefail

REPO_DIR="/home/enrico/homelab"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_TAG="auto-deploy"

[ -f "$SCRIPT_DIR/.env.cluster" ] && source "$SCRIPT_DIR/.env.cluster"
# shellcheck source=scripts/lib/telegram.sh
source "$SCRIPT_DIR/lib/telegram.sh" 2>/dev/null || send_telegram() { :; }

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

# Sync heavy's clone and rebuild MC if needed (single SSH call)
log "Syncing heavy clone..."
mc_result=$(ssh -o ConnectTimeout=5 -o BatchMode=yes heavy bash -s "$LOCAL" "$REMOTE" <<'HEAVY_SYNC' 2>&1) || true
  cd /home/enrico/pi-cluster || exit 0
  git pull --ff-only origin master --quiet 2>/dev/null || exit 0
  if git diff --name-only "$1..$2" 2>/dev/null | grep -q '^mission-control/'; then
    cd /home/enrico/mission-control && docker compose up -d --no-deps --build api 2>&1 && docker compose restart proxy 2>&1
    echo "MC_REBUILT"
  fi
HEAVY_SYNC
if echo "$mc_result" | grep -q "MC_REBUILT"; then
    log "Mission Control rebuilt on heavy"
fi

log "Deploy complete"

send_telegram "🚀 *Auto-Deploy*
$COMMITS"
