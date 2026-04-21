#!/bin/bash
# Claude Code Stop hook — async, may not fire in all contexts.
# The 15-min timer and nightly consolidation are safety nets.
# Must never fail — exit 0 always.
/usr/bin/python3 /home/enrico/life/scripts/cc_session_digest.py || true
/home/enrico/life/scripts/mini-consolidate.sh || true

# Clean up squash-merged local branches and stale worktrees
if command -v gh >/dev/null 2>&1; then
    _repo_dir="$HOME/pi-cluster"
    _branch_count=$(git -C "$_repo_dir" branch --format='%(refname:short)' | grep -cv '^master$' 2>/dev/null || echo 0)
    if [ "$_branch_count" -gt 0 ]; then
        _merged=$(gh pr list --repo enricoaquilina/pi-cluster --state merged --limit 100 \
            --json headRefName --jq '.[].headRefName' 2>/dev/null) || _merged=""
        if [ -n "$_merged" ]; then
            for _b in $(git -C "$_repo_dir" branch --format='%(refname:short)' | grep -v '^master$'); do
                if echo "$_merged" | grep -qxF "$_b"; then
                    git -C "$_repo_dir" branch -D "$_b" 2>/dev/null || true
                fi
            done
        fi
    fi
fi
git -C "$HOME/pi-cluster" worktree prune 2>/dev/null || true

# Sync ~/life to git (capture session changes)
if [ -d "$HOME/life/.git" ]; then
    # shellcheck source=/dev/null
    for _lib in "$HOME/pi-cluster/life-automation/lib/life-git-sync.sh" \
                "$HOME/life/scripts/lib/life-git-sync.sh"; do
        [ -f "$_lib" ] && { source "$_lib"; break; }
    done
    life_git_sync "$HOME/life" || true
fi
