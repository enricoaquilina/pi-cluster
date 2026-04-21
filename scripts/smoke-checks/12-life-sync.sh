#!/bin/bash
# Checks: ~/life git repo sync freshness + integrity

check_life_sync() {
    local life_dir="$HOME/life"
    if [ ! -d "$life_dir/.git" ]; then
        check_service "life-sync" "down" "$HOME/life is not a git repo"
        return
    fi
    # Check last commit age < 48h
    local last_commit_ts
    last_commit_ts=$(git -C "$life_dir" log -1 --format='%ct' 2>/dev/null || echo 0)
    local now_ts
    now_ts=$(date +%s)
    local age_hours=$(( (now_ts - last_commit_ts) / 3600 ))
    if [ "$age_hours" -gt 48 ]; then
        check_service "life-sync" "degraded" "last commit ${age_hours}h ago (>48h)"
        return
    fi
    # Check git fsck
    if ! git -C "$life_dir" fsck --no-dangling 2>/dev/null; then
        check_service "life-sync" "degraded" "git fsck failed"
        return
    fi
    check_service "life-sync" "up"
}
