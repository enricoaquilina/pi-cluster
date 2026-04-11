#!/bin/bash
# Claude Code SessionStart hook — briefs on Maxwell activity.
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
# Phase 8B: segment-match resolution via config/project-slugs.json.
# The pre-v3 substring match (`*/pi-cluster*`) misfired on paths like
# `/tmp/not-pi-cluster-backup`. Instead we walk $PWD segments from the
# deepest ancestor upward and return the first exact segment match.
SESSION_SEARCH="$LIFE_DIR/scripts/session_search.py"
SLUGS_CONFIG="$HOME/pi-cluster/life-automation/config/project-slugs.json"
[ -f "$SLUGS_CONFIG" ] || SLUGS_CONFIG="$LIFE_DIR/scripts/config/project-slugs.json"
PROJECT_SLUG=""
if [ -f "$SLUGS_CONFIG" ]; then
    PROJECT_SLUG=$(/usr/bin/python3 - "$PWD" "$SLUGS_CONFIG" <<'PY' 2>/dev/null
import json, sys
pwd = sys.argv[1]
cfg_path = sys.argv[2]
try:
    cfg = json.load(open(cfg_path))
    slugs = cfg.get("slugs", {})
except Exception:
    sys.exit(0)

# Build segment → slug map (first segment wins if duplicate)
seg_to_slug = {}
for slug, data in slugs.items():
    for seg in data.get("segments", [slug]):
        seg_to_slug.setdefault(seg, slug)

# Walk segments from the deepest to the shallowest; first match wins.
parts = [p for p in pwd.split("/") if p]
for seg in reversed(parts):
    if seg in seg_to_slug:
        print(seg_to_slug[seg])
        break
PY
    )
fi

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
