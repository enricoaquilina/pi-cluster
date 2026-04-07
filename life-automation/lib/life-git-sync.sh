#!/bin/bash
# Git sync functions for ~/life knowledge base.
# Designed for cron/hook safety: never fails, never blocks, always exits 0.
#
# Usage:
#   source lib/life-git-sync.sh
#   life_git_sync [dir]   # add, commit, push
#   life_git_pull [dir]   # pull --rebase --autostash
#
# Set LIFE_GIT_SYNC_DISABLED=1 to skip all operations (maintenance mode).

life_git_sync() {
    [ "${LIFE_GIT_SYNC_DISABLED:-0}" = "1" ] && return 0

    local LIFE_DIR="${1:-$HOME/life}"
    cd "$LIFE_DIR" || return 0
    git rev-parse --git-dir >/dev/null 2>&1 || return 0

    # Stage all changes (.gitignore handles exclusions)
    git add -A 2>/dev/null

    # Only commit if there are staged changes
    if ! git diff --cached --quiet 2>/dev/null; then
        local stats
        stats=$(git diff --cached --stat 2>/dev/null | tail -1)
        git commit -m "auto: $(hostname) $(date +%Y-%m-%d-%H%M) [${stats}]" 2>/dev/null || return 0

        if ! timeout 30 git push origin main 2>/dev/null; then
            _life_sync_alert "git push failed on $(hostname)" 2>/dev/null
        fi
    fi
}

life_git_pull() {
    [ "${LIFE_GIT_SYNC_DISABLED:-0}" = "1" ] && return 0

    local LIFE_DIR="${1:-$HOME/life}"
    cd "$LIFE_DIR" || return 0
    git rev-parse --git-dir >/dev/null 2>&1 || return 0

    # Check if remote exists
    git remote get-url origin >/dev/null 2>&1 || return 0

    if ! timeout 30 git pull --rebase --autostash origin main 2>/dev/null; then
        # If rebase fails (conflict), abort and alert
        git rebase --abort 2>/dev/null
        _life_sync_alert "git pull conflict on $(hostname) — manual resolution needed" 2>/dev/null
    fi
}

_life_sync_alert() {
    local msg="$1"
    # Use send_telegram if already sourced (by caller script), otherwise try to source it
    if ! type send_telegram >/dev/null 2>&1; then
        local TELEGRAM_LIB
        for lib in "$HOME/pi-cluster/scripts/lib/telegram.sh" \
                   "$HOME/life/scripts/../../../pi-cluster/scripts/lib/telegram.sh"; do
            [ -f "$lib" ] && TELEGRAM_LIB="$lib" && break
        done
        if [ -n "${TELEGRAM_LIB:-}" ]; then
            # shellcheck source=/dev/null
            source "$TELEGRAM_LIB" 2>/dev/null || return 0
            # Need env vars for telegram
            local ENV_FILE="$HOME/pi-cluster/scripts/.env.cluster"
            # shellcheck source=/dev/null
            [ -f "$ENV_FILE" ] && source "$ENV_FILE" 2>/dev/null
        fi
    fi
    if type send_telegram >/dev/null 2>&1; then
        send_telegram "⚠️ ~/life sync: $msg" 2>/dev/null || true
    fi
}
