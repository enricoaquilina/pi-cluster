#!/bin/bash
# Claude Code SessionStart hook — briefs on Maxwell activity.
# Output goes to stdout → injected into Claude Code context (max 10K chars).
# Uses absolute paths for non-interactive shell safety.
LIFE_DIR="${LIFE_DIR:-$HOME/life}"
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
