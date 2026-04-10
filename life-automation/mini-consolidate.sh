#!/bin/bash
# Mini-consolidation: lightweight incremental extraction.
# Called by Stop hook (async) and 15-min systemd timer.
# Shares consolidate.lock with nightly — skip if nightly is running.
set -euo pipefail

LIFE_DIR="${LIFE_DIR:-$HOME/life}"
LOG_DIR="$LIFE_DIR/logs"
TODAY=$(date '+%Y-%m-%d')
YEAR=$(date '+%Y')
MONTH=$(date '+%m')
LOCK_FILE="$LOG_DIR/consolidate.lock"
STATE_FILE="$LOG_DIR/.mini-consolidate-mtime"
DAILY_NOTE="$LIFE_DIR/Daily/$YEAR/$MONTH/$TODAY.md"

mkdir -p "$LOG_DIR"

# Shared lock with nightly consolidation — skip if already running
exec 9>"$LOCK_FILE"
flock -n 9 || exit 0

# 1. Run session digest in scan mode (catches unprocessed transcripts)
/usr/bin/python3 "$LIFE_DIR/scripts/cc_session_digest.py" --scan 2>/dev/null || true

# 2. Run ingest_dispatches.py (get Maxwell's latest activity)
timeout 15 /usr/bin/python3 "$LIFE_DIR/scripts/ingest_dispatches.py" 2>/dev/null || true

# 2b. Incremental QMD embedding (skip if embedded <10 min ago)
QMD_BIN="$HOME/.local/bin/qmd"
EMBED_STATE="$LOG_DIR/.qmd-embed-mtime"
if [ -x "$QMD_BIN" ]; then
    LAST_EMBED=$(cat "$EMBED_STATE" 2>/dev/null || echo 0)
    [[ "$LAST_EMBED" =~ ^[0-9]+$ ]] || LAST_EMBED=0
    NOW=$(date +%s)
    if [ $(( NOW - LAST_EMBED )) -gt 600 ]; then
        timeout 240 "$QMD_BIN" embed 2>/dev/null && echo "$NOW" > "$EMBED_STATE" || true
    fi
fi

# 3. Check if daily note has changed since last mini-consolidation
[ -f "$DAILY_NOTE" ] || exit 0
CURRENT_MTIME=$(stat -c %Y "$DAILY_NOTE" 2>/dev/null || echo 0)
LAST_MTIME=$(cat "$STATE_FILE" 2>/dev/null || echo 0)
[ "$CURRENT_MTIME" -gt "$LAST_MTIME" ] || exit 0

# 4. Haiku extraction would go here (deferred — nightly handles this for now)
# TODO: Add lightweight Haiku extraction call for real-time entity updates
# For now, mini-consolidation just ensures session digests and maxwell notes
# are captured. Entity extraction happens at nightly consolidation.

# 5. Sync daily note + skill pointers to openclaw gateway
if [[ "${LIFE_SYNC_ENABLED:-1}" != "0" ]]; then
    timeout 60 bash "$(dirname "$0")/sync-openclaw-memory.sh" 2>&1 | head -5 >> "$LOG_DIR/consolidate.log" || {
        echo "$(date -Is) ERROR: sync-openclaw-memory exited $?" >> "$LOG_DIR/consolidate.log"
    }
fi

echo "$CURRENT_MTIME" > "$STATE_FILE"
