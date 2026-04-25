#!/bin/bash
# One-time backfill: run extraction + staging for daily notes that missed nightly consolidation.
# Usage: bash backfill-consolidation.sh [date1 date2 ...]
# Default: all dates with >16 lines of content in April 2026 that haven't been processed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/life-automation-lib.sh"

life_init_env
TAG="consolidate"
log() { life_log "$TAG" "$*"; }

life_check_topology || exit 1
life_require_claude_cli || { log "ERROR: claude CLI not found at $CLAUDE_BIN"; exit 1; }

EXISTING=$(find "$LIFE_DIR/Projects/" "$LIFE_DIR/People/" "$LIFE_DIR/Companies/" \
    -mindepth 1 -maxdepth 1 -type d -not -name '_template' -printf '%f\n' 2>/dev/null \
    | sort -u || true)

# Collect recent facts for contradiction detection
RECENT_FACTS=$(python3 -c "
import json, sys
from pathlib import Path
from datetime import date, timedelta
cutoff = str(date.today() - timedelta(days=30))
life = Path(sys.argv[1])
for pattern in ['Projects/*/items.json', 'People/*/items.json', 'Companies/*/items.json']:
    for items_path in sorted(life.glob(pattern)):
        try:
            items = json.loads(items_path.read_text())
        except (json.JSONDecodeError, FileNotFoundError):
            continue
        entity = items_path.parent.name
        for item in items:
            if item.get('date', '') >= cutoff and item.get('confidence') not in ('archived', 'stale', 'superseded'):
                print(f'- [{entity}] {item[\"fact\"]}')
" "$LIFE_DIR" 2>/dev/null | head -60 || true)

# Determine dates to process
if [ $# -gt 0 ]; then
    DATES=("$@")
else
    DATES=(2026-04-09 2026-04-17 2026-04-20 2026-04-21 2026-04-22 2026-04-23)
fi

TOTAL=${#DATES[@]}
PROCESSED=0
SKIPPED=0
FAILED=0

for TARGET_DATE in "${DATES[@]}"; do
    YEAR="${TARGET_DATE:0:4}"
    MONTH="${TARGET_DATE:5:2}"
    NOTE="$LIFE_DIR/Daily/$YEAR/$MONTH/$TARGET_DATE.md"

    if [ ! -f "$NOTE" ]; then
        log "SKIP $TARGET_DATE — no daily note"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    LINE_COUNT=$(wc -l < "$NOTE")
    if [ "$LINE_COUNT" -le 16 ]; then
        log "SKIP $TARGET_DATE — only $LINE_COUNT lines (empty template)"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    log "Processing $TARGET_DATE ($LINE_COUNT lines)..."

    DAILY_CONTENT=$(cat "$NOTE")
    if [ ${#DAILY_CONTENT} -gt 50000 ]; then
        DAILY_CONTENT="${DAILY_CONTENT:0:50000}
[... truncated for consolidation ...]"
    fi

    TMPFILE=$(mktemp /tmp/life-backfill-XXXXXX.json)

    PROMPT="You are a knowledge consolidation assistant. Analyze the daily note below and output ONLY valid JSON. No markdown, no code fences, no explanation text.

TODAY: $TARGET_DATE
EXISTING ENTITIES: $EXISTING

RECENT FACTS (last 30 days — check for contradictions):
$RECENT_FACTS

DAILY NOTE:
$DAILY_CONTENT

Output this exact JSON schema (use empty arrays if nothing qualifies):
{
  \"new_entities\": [{\"type\": \"person|project|company\", \"name\": \"slug-lowercase-hyphens\", \"display\": \"Display Name\"}],
  \"fact_updates\": [{\"entity_type\": \"project|person|company|area|resource\", \"entity\": \"existing-slug\", \"date\": \"$TARGET_DATE\", \"fact\": \"one sentence\", \"category\": \"deployment|decision|configuration|lesson|preference|event|pending\", \"temporal\": false}],
  \"tacit_knowledge\": [{\"file\": \"workflow-habits|hard-rules|communication-preferences|lessons-learned\", \"entry\": \"one sentence\"}],
  \"skills\": [{\"name\": \"slug-name\", \"display\": \"How to Do X\", \"steps\": [\"step 1\", \"step 2\"]}],
  \"relationships\": [{\"from\": \"slug\", \"from_type\": \"person|project|company\", \"to\": \"slug\", \"to_type\": \"person|project|company\", \"relation\": \"works-on|owns|provides|uses|reports-to|manages|contributes-to\"}],
  \"summary\": \"one sentence\"
}

Rules:
- Entity slugs MUST be lowercase-hyphens only
- new_entities: only for entities with significant discussion (3+ mentions or ongoing relationship)
- fact_updates: only for EXISTING entities listed above
- If a new fact CONTRADICTS a recent fact above, add \"supersedes\": \"exact text of old fact\"
- Set \"temporal\": true for facts about current states that will change. Default false.
- tacit_knowledge: only genuinely new rules or lessons
- Return empty arrays if nothing qualifies"

    if ! timeout 120 "$CLAUDE_BIN" -p "$PROMPT" \
        --output-format text \
        --no-session-persistence \
        --model claude-haiku-4-5-20251001 \
        > "$TMPFILE" 2>/dev/null; then
        log "ERROR: Claude failed for $TARGET_DATE — skipping"
        rm -f "$TMPFILE"
        FAILED=$((FAILED + 1))
        continue
    fi

    # Validate JSON
    if ! python3 -c "
import json, sys, importlib.util
spec = importlib.util.spec_from_file_location('ae', '$LIFE_DIR/scripts/apply_extraction.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
json.loads(mod.strip_fences(open(sys.argv[1]).read()))
" "$TMPFILE" 2>/dev/null; then
        log "ERROR: Invalid JSON for $TARGET_DATE — saved to backfill-errors-$TARGET_DATE.json"
        cp "$TMPFILE" "$LOG_DIR/backfill-errors-$TARGET_DATE.json"
        rm -f "$TMPFILE"
        FAILED=$((FAILED + 1))
        continue
    fi

    # Apply with staging
    export CONSOLIDATION_DATE="$TARGET_DATE"
    python3 "$LIFE_DIR/scripts/apply_extraction.py" --stage < "$TMPFILE" \
        | tee -a "$LOG_DIR/consolidate.log"

    rm -f "$TMPFILE"
    PROCESSED=$((PROCESSED + 1))
    log "Done $TARGET_DATE"

    sleep 2
done

# Auto-graduate all qualifying candidates from backfill
log "Auto-graduating backfilled candidates..."
python3 "$LIFE_DIR/scripts/review.py" auto-graduate 2>&1 | tee -a "$LOG_DIR/consolidate.log"
python3 "$LIFE_DIR/scripts/review.py" queue 2>&1 | tee -a "$LOG_DIR/consolidate.log"

log "Backfill complete: $PROCESSED processed, $SKIPPED skipped, $FAILED failed (of $TOTAL total)"
