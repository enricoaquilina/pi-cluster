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
# shellcheck source=scripts/lib/deploy-rollback.sh
source "$SCRIPT_DIR/lib/deploy-rollback.sh"
# shellcheck source=scripts/lib/deploy-notify-pr.sh
source "$SCRIPT_DIR/lib/deploy-notify-pr.sh" 2>/dev/null || deploy_notify_pr() { :; }

log() { logger -t "$LOG_TAG" "$1"; }

cd "$REPO_DIR" || exit 1

# Prevent overlapping runs (2-min timer can overlap long deploys)
exec 9> /tmp/auto-deploy.lock
if ! flock -n 9; then
    log "another deploy in progress; skipping"
    exit 0
fi

DEPLOY_START=$(date +%s)

# Heartbeat for staleness detection (runs even on no-op)
echo "$DEPLOY_START" > /tmp/auto-deploy-heartbeat

# Fetch latest from origin
git fetch origin master --quiet 2>/dev/null || exit 0

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/master)

# Nothing to do if up to date
[ "$LOCAL" = "$REMOTE" ] && exit 0

# Detect and recover from dirty working tree
if ! git diff-index --quiet HEAD --; then
    dirty_files=$(git diff --name-only HEAD 2>&1 | head -5)
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
CHANGED_FILES=$(git diff --name-only "$LOCAL..$REMOTE" 2>&1)
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

# Verify heavy containers after deploy sync
if echo "$mc_result" | grep -qE "MC_REBUILT|PROXY_RESTARTED|NODES_REPAIRED"; then
    sleep 5
    heavy_health=$(ssh -o ConnectTimeout=5 -o BatchMode=yes heavy \
        "docker ps --filter health=unhealthy --format '{{.Names}}' 2>/dev/null" 2>/dev/null || echo "")
    if [ -n "$heavy_health" ]; then
        log "WARNING: unhealthy containers on heavy: $heavy_health"
        send_telegram "⚠️ *Auto-Deploy*: unhealthy containers on heavy post-sync: \`$heavy_health\`"
    fi
fi

deploy_log_metrics() {
    local smoke_result="${1:-ok}" rolled_back="${2:-false}"
    local duration=$(( $(date +%s) - DEPLOY_START ))
    printf '{"ts":"%s","before":"%s","after":"%s","smoke":"%s","rollback":%s,"duration_s":%d}\n' \
        "$(date -Iseconds)" "$LOCAL" "$(git rev-parse HEAD)" \
        "$smoke_result" "$rolled_back" "$duration" \
        >> /var/log/deploy-history.jsonl 2>/dev/null || true
}

# Post-deploy smoke check (critical services only, 90s timeout)
log "Running post-deploy smoke check..."
SMOKE_OUTPUT=$(timeout 90 bash "$SCRIPT_DIR/deploy-smoke.sh" 2>&1) || true

FAILED_MSG=$(git log --format='%s' "$REMOTE" -1 2>/dev/null || echo "")

if echo "$SMOKE_OUTPUT" | grep -q "DEPLOY_SMOKE_FAIL"; then
    FAILED_SVCS=$(echo "$SMOKE_OUTPUT" | grep "DEPLOY_SMOKE_FAIL" | cut -d: -f2-)
    log "ERROR: Post-deploy smoke FAILED:$FAILED_SVCS"

    if deploy_can_rollback; then
        if deploy_rollback; then
            RECHECK=$(timeout 90 bash "$SCRIPT_DIR/deploy-smoke.sh" 2>&1) || true
            if echo "$RECHECK" | grep -q "DEPLOY_SMOKE_OK"; then
                deploy_log_metrics "fail" "true"
                send_telegram "🔄 *Auto-Deploy ROLLED BACK*
$COMMITS
Failed:$FAILED_SVCS
Rolled back to deploy/stable — services recovered"
                deploy_notify_pr "$FAILED_MSG" \
                    "## ⚠️ Deploy Rollback\n\nThis PR caused a post-deploy smoke failure.\n\n**Failed services:**$FAILED_SVCS\n**Action:** Rolled back to deploy/stable — services recovered"
            else
                deploy_log_metrics "fail" "true"
                send_telegram "🚨 *Auto-Deploy ROLLBACK FAILED*
$COMMITS
Failed:$FAILED_SVCS
Manual intervention needed"
                deploy_notify_pr "$FAILED_MSG" \
                    "## 🚨 Deploy Rollback FAILED\n\nThis PR caused a post-deploy smoke failure and rollback did not recover.\n\n**Failed services:**$FAILED_SVCS\n**Action:** Manual intervention needed"
            fi
        else
            deploy_log_metrics "fail" "true"
            send_telegram "🚨 *Auto-Deploy ROLLBACK FAILED*
$COMMITS
Failed:$FAILED_SVCS
Rollback command failed before recovery verification"
            deploy_notify_pr "$FAILED_MSG" \
                "## 🚨 Deploy Rollback FAILED\n\nThis PR caused a post-deploy smoke failure. Rollback command failed.\n\n**Failed services:**$FAILED_SVCS"
        fi
    else
        deploy_log_metrics "fail" "false"
        send_telegram "🚨 *Auto-Deploy SMOKE FAIL (no rollback tag)*
$COMMITS
Failed:$FAILED_SVCS"
        deploy_notify_pr "$FAILED_MSG" \
            "## 🚨 Deploy Smoke Failure\n\nThis PR caused a post-deploy smoke failure. No rollback tag available.\n\n**Failed services:**$FAILED_SVCS"
    fi
else
    log "Post-deploy smoke passed"
    deploy_mark_stable
    deploy_log_metrics "ok" "false"
    send_telegram "🚀 *Auto-Deploy*
$COMMITS"
fi

log "Deploy complete"
