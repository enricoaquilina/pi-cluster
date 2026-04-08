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

# --- Yesterday's session digests ---
YESTERDAY=$(date -d "yesterday" +%Y-%m-%d 2>/dev/null || date -v-1d +%Y-%m-%d 2>/dev/null)
YMONTH=$(date -d "yesterday" +%m 2>/dev/null || date -v-1d +%m 2>/dev/null)
DIGEST_FILE="$LIFE_DIR/Daily/$YEAR/$YMONTH/sessions-digest-${YESTERDAY}.jsonl"
if [ -f "$DIGEST_FILE" ]; then
    echo ""
    echo "## Yesterday's Sessions"
    /usr/bin/tail -3 "$DIGEST_FILE" | /usr/bin/python3 -c "
import sys, json
for line in sys.stdin:
    try:
        d = json.loads(line.strip())
        print(f\"- [{d.get('session_type','?')}] {d.get('summary','no summary')}\")
    except: pass
" 2>/dev/null
fi
