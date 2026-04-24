#!/bin/bash
# Claude Code SessionStart hook — assembles ~/life/ context for session.
# Output goes to stdout → injected into Claude Code context (max 10K chars).
# Uses absolute paths for non-interactive shell safety.
#
# Phase 8B hook-wide invariant: this script must ALWAYS exit 0 on every error
# path so a broken hook never blocks a Claude Code session start.
set +e
trap 'exit 0' EXIT

LIFE_DIR="${LIFE_DIR:-$HOME/life}"

# Verify critical system files match git HEAD (detect uncommitted tampering)
if [ -d "$LIFE_DIR/.git" ]; then
    for _f in Areas/about-me/hard-rules.md Areas/about-me/profile.md; do
        if [ -f "$LIFE_DIR/$_f" ] && ! git -C "$LIFE_DIR" diff --quiet HEAD -- "$_f" 2>/dev/null; then
            echo "## ⚠️ WARN: ~/life/$_f has uncommitted changes — verify they are intentional"
        fi
    done
fi
if [ -f "$HOME/CLAUDE.md" ] && [ -d "$HOME/.git" ]; then
    if ! git -C "$HOME" diff --quiet HEAD -- CLAUDE.md 2>/dev/null; then
        echo "## ⚠️ WARN: ~/CLAUDE.md has uncommitted changes — verify they are intentional"
    fi
fi

# Pull latest ~/life from git (sync from other nodes/sessions)
if [ -d "$LIFE_DIR/.git" ]; then
    # shellcheck source=/dev/null
    for _lib in "$HOME/pi-cluster/life-automation/lib/life-git-sync.sh" \
                "$LIFE_DIR/scripts/lib/life-git-sync.sh"; do
        [ -f "$_lib" ] && { source "$_lib"; break; }
    done
    life_git_pull "$LIFE_DIR" 2>/dev/null || true
fi

# --- Primary context assembly (tiered, budget-capped) ---
CONTEXT_BUDGET="$HOME/pi-cluster/life-automation/context_budget.py"
if [ -f "$CONTEXT_BUDGET" ]; then
    /usr/bin/python3 "$CONTEXT_BUDGET" --cwd "$PWD" --budget 6000 2>/dev/null
    exit 0
fi

# --- Fallback: legacy sections (if context_budget.py missing) ---
TODAY=$(date '+%Y-%m-%d')
YEAR=$(date '+%Y')
MONTH=$(date '+%m')

MAXWELL="$LIFE_DIR/Daily/$YEAR/$MONTH/maxwell-$TODAY.md"
echo "## Maxwell Activity"
if [[ -f "$MAXWELL" ]]; then
    /usr/bin/sed -n '/^## /,$ p' "$MAXWELL" | head -20
else
    echo "_No Maxwell dispatches today._"
fi

DAILY_NOTE="$LIFE_DIR/Daily/$YEAR/$MONTH/$TODAY.md"
if [ -f "$DAILY_NOTE" ]; then
    echo ""
    echo "## Daily Note Summary"
    /usr/bin/awk '/^## [^A]/ && p{p=0} /^## Active Projects/{p=1} p' "$DAILY_NOTE" 2>/dev/null | head -20
    /usr/bin/awk '/^## [^P]/ && p{p=0} /^## Pending Items/{p=1} p' "$DAILY_NOTE" 2>/dev/null | head -10
fi

SESSION_SEARCH="$LIFE_DIR/scripts/session_search.py"
if [ -f "$SESSION_SEARCH" ]; then
    echo ""
    echo "## Recent Sessions"
    /usr/bin/python3 "$SESSION_SEARCH" --recent 5 --json 2>/dev/null | /usr/bin/python3 -c "
import sys, json
try:
    for d in json.load(sys.stdin):
        ts = d.get('ts','')[:10]
        stype = d.get('session_type','?')
        summary = d.get('summary','')[:120]
        print(f'- {ts} [{stype}] {summary}')
except: pass
" 2>/dev/null
fi

CROSS_PLATFORM="$LIFE_DIR/scripts/cross_platform_summary.py"
if [ -f "$CROSS_PLATFORM" ]; then
    XPLAT=$(/usr/bin/python3 "$CROSS_PLATFORM" --hours 24 --exclude-platform claude-code --max-lines 15 2>/dev/null)
    if [ -n "$XPLAT" ]; then
        echo ""
        echo "$XPLAT"
    fi
fi
