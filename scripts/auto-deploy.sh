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

# Detect and recover from dirty working tree
if ! git diff-index --quiet HEAD -- 2>/dev/null; then
    dirty_files=$(git diff --name-only HEAD 2>/dev/null | head -5)
    log "WARNING: dirty working tree, stashing: $dirty_files"
    send_telegram "⚠️ *Auto-Deploy*: dirty tree detected, stashing
\`$dirty_files\`"
    if ! git stash --include-untracked 2>/dev/null; then
        log "ERROR: git stash failed"
        send_telegram "🚨 *Auto-Deploy BLOCKED*: cannot stash dirty tree
\`$dirty_files\`"
        exit 1
    fi
fi

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

log "Running monitoring stack playbook..."
ansible-playbook "$REPO_DIR/playbooks/monitoring.yml" --quiet 2>&1 | while read -r line; do log "mon-stack: $line"; done

# Deploy node scripts to slave nodes if changed
CHANGED_FILES=$(git diff --name-only "$LOCAL..$REMOTE" 2>/dev/null)
if echo "$CHANGED_FILES" | grep -qE '^scripts/openclaw-node-agent\.py|^playbooks/openclaw-nodes\.yml'; then
    log "Running openclaw-nodes playbook (node-agent changed)..."
    ansible-playbook "$REPO_DIR/playbooks/openclaw-nodes.yml" --quiet 2>&1 | while read -r line; do log "nodes: $line"; done
fi

# Sync heavy's clone and rebuild MC if needed (single SSH call)
# Smart rebuild: only docker build when Dockerfile/requirements change,
# skip pip when only app code changes (layer cache handles it),
# proxy-only restart for frontend/Caddyfile changes (bind-mounted).
# 10-min cooldown prevents rebuild storms (the incident that killed the SSD).
log "Syncing heavy clone..."
mc_result=$(ssh -o ConnectTimeout=5 -o BatchMode=yes heavy bash -s "$LOCAL" "$REMOTE" <<'HEAVY_SYNC' 2>&1) || true
  cd /home/enrico/pi-cluster || exit 0
  git pull --ff-only origin master --quiet 2>/dev/null || exit 0

  CHANGED=$(git diff --name-only "$1..$2" 2>/dev/null)
  [ -z "$CHANGED" ] && exit 0

  MC_DIR="/home/enrico/mission-control"
  COOLDOWN_FILE="/tmp/mc-last-build-ts"
  COOLDOWN_SECS=600  # 10 minutes

  # Check build cooldown
  build_allowed=true
  if [ -f "$COOLDOWN_FILE" ]; then
    last_build=$(cat "$COOLDOWN_FILE" 2>/dev/null || echo 0)
    now=$(date +%s)
    elapsed=$(( now - last_build ))
    if [ "$elapsed" -lt "$COOLDOWN_SECS" ]; then
      build_allowed=false
      echo "BUILD_COOLDOWN (${elapsed}s/${COOLDOWN_SECS}s)"
    fi
  fi

  if echo "$CHANGED" | grep -q '^mission-control/'; then
    # Check if Dockerfile or dependency files changed (needs full rebuild)
    if echo "$CHANGED" | grep -qE '^mission-control/backend/(Dockerfile|requirements\.txt|constraints\.txt)'; then
      if [ "$build_allowed" = true ]; then
        cd "$MC_DIR" && docker compose up -d --no-deps --build api 2>&1
        date +%s > "$COOLDOWN_FILE"
        echo "MC_REBUILT"
      fi
    # Backend code-only changes still need --build (code is COPYed, not bind-mounted)
    # but pip layer is cached so this is fast and low-write
    elif echo "$CHANGED" | grep -q '^mission-control/backend/'; then
      if [ "$build_allowed" = true ]; then
        cd "$MC_DIR" && docker compose up -d --no-deps --build api 2>&1
        date +%s > "$COOLDOWN_FILE"
        echo "MC_REBUILT"
      fi
    fi
    # Frontend/Caddyfile changes: bind-mounted, just restart proxy (no build)
    if echo "$CHANGED" | grep -qE '^mission-control/(frontend/|Caddyfile)'; then
      cd "$MC_DIR" && docker compose restart proxy 2>&1
      echo "PROXY_RESTARTED"
    fi
  fi

  # Re-pair nodes if openclaw config changed (gateway recreate invalidates sessions)
  if echo "$CHANGED" | grep -qE 'scripts/openclaw|templates/openclaw'; then
    timeout 90 bash /home/enrico/pi-cluster/scripts/openclaw-pair-nodes.sh 2>&1
    echo "NODES_REPAIRED"
  fi
HEAVY_SYNC
if echo "$mc_result" | grep -q "MC_REBUILT"; then
    log "Mission Control rebuilt on heavy"
fi
if echo "$mc_result" | grep -q "PROXY_RESTARTED"; then
    log "MC proxy restarted on heavy (frontend/config change)"
fi
if echo "$mc_result" | grep -q "BUILD_COOLDOWN"; then
    log "MC build skipped on heavy (cooldown active)"
fi
if echo "$mc_result" | grep -q "NODES_REPAIRED"; then
    log "OpenClaw nodes re-paired on heavy"
fi

log "Deploy complete"

send_telegram "🚀 *Auto-Deploy*
$COMMITS"
