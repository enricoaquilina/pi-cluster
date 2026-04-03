#!/bin/bash
# Claude Code Stop hook — async, may not fire in all contexts.
# The 15-min timer and nightly consolidation are safety nets.
# Must never fail — exit 0 always.
/usr/bin/python3 /home/enrico/life/scripts/cc_session_digest.py || true
/home/enrico/life/scripts/mini-consolidate.sh || true

# Clean up merged local branches and stale worktrees
if command -v git >/dev/null 2>&1; then
    git -C /home/enrico/pi-cluster branch --merged master 2>/dev/null \
        | grep -v '^\*\|master' | xargs -r git -C /home/enrico/pi-cluster branch -d 2>/dev/null || true
    git -C /home/enrico/pi-cluster worktree prune 2>/dev/null || true
fi
