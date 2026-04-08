#!/bin/bash
# Claude Code SessionStart hook — briefs on Maxwell activity.
# Output goes to stdout → injected into Claude Code context (max 10K chars).
# Uses absolute paths for non-interactive shell safety.
LIFE_DIR="${LIFE_DIR:-$HOME/life}"

# Pull latest ~/life from git (sync from other nodes/sessions)
if [ -d "$LIFE_DIR/.git" ]; then
    # shellcheck source=/dev/null
    for _lib in "$HOME/pi-cluster/life-automation/lib/life-git-sync.sh" \
                "$LIFE_DIR/scripts/lib/life-git-sync.sh"; do
        [ -f "$_lib" ] && { source "$_lib"; break; }
    done
    life_git_pull "$LIFE_DIR" 2>/dev/null || true
fi

TODAY=$(date '+%Y-%m-%d')
YEAR=$(date '+%Y')
MONTH=$(date '+%m')

MAXWELL="$LIFE_DIR/Daily/$YEAR/$MONTH/maxwell-$TODAY.md"
AGENT_RUNS="$LIFE_DIR/logs/agent-runs.json"

echo "## Maxwell Activity"
if [[ -f "$MAXWELL" ]]; then
    # Print dispatch lines (skip YAML frontmatter)
    /usr/bin/sed -n '/^## /,$ p' "$MAXWELL" | head -20
else
    echo "_No Maxwell dispatches today._"
fi

# Last 3 heartbeat actions
if [[ -f "$AGENT_RUNS" ]]; then
    echo ""
    echo "## Recent Heartbeat"
    /usr/bin/python3 -c "
import json, sys
try:
    data = json.load(open(sys.argv[1]))
    for entry in data[-3:]:
        ts = entry.get('timestamp','')[:16]
        for a in entry.get('actions', []):
            title = a.get('title', '?')
            action = a.get('action', '?')
            persona = a.get('persona', '?')
            print(f'- {ts}: {title} -> {action} ({persona})')
except Exception:
    print('_Could not read agent runs._')
" "$AGENT_RUNS" 2>/dev/null || echo "_Could not read agent runs._"
fi

# --- Daily context (active projects + pending items) ---
DAILY_NOTE="$LIFE_DIR/Daily/$YEAR/$MONTH/$TODAY.md"
if [ -f "$DAILY_NOTE" ]; then
    echo ""
    echo "## Daily Note Summary"
    /usr/bin/awk '/^## [^A]/ && p{p=0} /^## Active Projects/{p=1} p' "$DAILY_NOTE" 2>/dev/null | head -20
    /usr/bin/awk '/^## [^P]/ && p{p=0} /^## Pending Items/{p=1} p' "$DAILY_NOTE" 2>/dev/null | head -10
fi

# --- Project context (detect from working directory) ---
SESSION_SEARCH="$LIFE_DIR/scripts/session_search.py"
PROJECT_SLUG=""
case "$PWD" in
    */pi-cluster*) PROJECT_SLUG="pi-cluster" ;;
    */polymarket*) PROJECT_SLUG="polymarket-bot" ;;
    */gym-tracker*) PROJECT_SLUG="gym-tracker-app" ;;
    */openclaw*|*/maxwell*) PROJECT_SLUG="openclaw-maxwell" ;;
esac

if [ -n "$PROJECT_SLUG" ] && [ -f "$SESSION_SEARCH" ]; then
    echo ""
    echo "## Project Context: $PROJECT_SLUG"
    # Show entity summary first paragraph
    PROJ_SUMMARY="$LIFE_DIR/Projects/$PROJECT_SLUG/summary.md"
    if [ -f "$PROJ_SUMMARY" ]; then
        /usr/bin/awk '/^---/{f++} f==2{p=1;next} p && /^$/{exit} p' "$PROJ_SUMMARY" 2>/dev/null | head -3
    fi
    # Show related sessions
    echo ""
    echo "### Related Sessions"
    /usr/bin/python3 "$SESSION_SEARCH" --query "$PROJECT_SLUG" --recent 3 --json 2>/dev/null | /usr/bin/python3 -c "
import sys, json
try:
    for d in json.load(sys.stdin):
        ts = d.get('ts','')[:10]
        stype = d.get('session_type','?')
        summary = d.get('summary','')[:100]
        print(f'- {ts} [{stype}] {summary}')
except: pass
" 2>/dev/null
fi

# --- Recent sessions (from FTS5 search) ---
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
