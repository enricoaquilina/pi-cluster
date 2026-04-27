#!/bin/bash
# Deploy rollback helpers — tag-based stable marker
# Sourced by auto-deploy.sh

STABLE_TAG="deploy/stable"

deploy_mark_stable() {
    local head
    head=$(git rev-parse HEAD)
    git tag -f "$STABLE_TAG" "$head" 2>/dev/null
    log "Marked ${head:0:7} as $STABLE_TAG"
}

deploy_can_rollback() {
    local stable_ref head
    stable_ref=$(git rev-parse "$STABLE_TAG" 2>/dev/null) || return 1
    head=$(git rev-parse HEAD)
    [ "$stable_ref" != "$head" ]
}

deploy_rollback() {
    local stable_ref
    stable_ref=$(git rev-parse "$STABLE_TAG" 2>/dev/null) || {
        log "ERROR: No $STABLE_TAG tag — cannot rollback"
        return 1
    }

    local current_head
    current_head=$(git rev-parse --short HEAD) || return 1
    log "ROLLING BACK: ${current_head} -> ${stable_ref:0:7}"

    git reset --hard "$STABLE_TAG" 2>&1 | while read -r line; do log "$line"; done
    local rc=${PIPESTATUS[0]}
    (( rc == 0 )) || {
        log "ERROR: git reset failed"
        return 1
    }

    log "Re-running playbooks at stable version..."
    ansible-playbook "$REPO_DIR/playbooks/openclaw-monitoring.yml" --quiet 2>&1 | while read -r line; do log "rollback-monitoring: $line"; done
    rc=${PIPESTATUS[0]}
    (( rc == 0 )) || {
        log "ERROR: rollback openclaw-monitoring failed"
        return 1
    }

    ansible-playbook "$REPO_DIR/playbooks/monitoring.yml" --quiet 2>&1 | while read -r line; do log "rollback-mon-stack: $line"; done
    rc=${PIPESTATUS[0]}
    (( rc == 0 )) || {
        log "ERROR: rollback monitoring stack failed"
        return 1
    }

    ssh -o ConnectTimeout=5 -o BatchMode=yes heavy \
        "cd /home/enrico/pi-cluster && git fetch origin && git reset --hard '$STABLE_TAG'"
    rc=$?
    (( rc == 0 )) || {
        log "ERROR: remote heavy rollback failed"
        return 1
    }

    log "Verifying heavy containers post-rollback..."
    local unhealthy
    unhealthy=$(ssh -o ConnectTimeout=5 -o BatchMode=yes heavy \
        "docker ps --filter health=unhealthy --format '{{.Names}}'" 2>/dev/null || echo "")
    if [ -n "$unhealthy" ]; then
        log "WARNING: unhealthy containers on heavy after rollback: $unhealthy"
    fi

    return 0
}
