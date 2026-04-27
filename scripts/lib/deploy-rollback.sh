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
    stable_ref=$(git rev-parse "$STABLE_TAG" 2>/dev/null)
    if [ -z "$stable_ref" ]; then
        log "ERROR: No $STABLE_TAG tag — cannot rollback"
        return 1
    fi

    local current_head
    current_head=$(git rev-parse --short HEAD)
    log "ROLLING BACK: ${current_head} -> ${stable_ref:0:7}"

    git reset --hard "$STABLE_TAG" 2>&1 | while read -r line; do log "$line"; done

    log "Re-running playbooks at stable version..."
    ansible-playbook "$REPO_DIR/playbooks/openclaw-monitoring.yml" --quiet 2>&1 | while read -r line; do log "rollback-monitoring: $line"; done
    ansible-playbook "$REPO_DIR/playbooks/monitoring.yml" --quiet 2>&1 | while read -r line; do log "rollback-mon-stack: $line"; done

    ssh -o ConnectTimeout=5 -o BatchMode=yes heavy \
        "cd /home/enrico/pi-cluster && git fetch origin && git reset --hard $STABLE_TAG" 2>/dev/null || true

    return 0
}
