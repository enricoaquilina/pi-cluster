#!/bin/bash
set -euo pipefail

# Cron safety: explicit PATH, HOME, locale
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
export HOME="${HOME:-/home/enrico}"
export LANG="en_US.UTF-8"
export PYTHONIOENCODING="utf-8"

readonly LIFE_DIR="$HOME/life"
readonly CLAUDE_BIN="$HOME/.local/bin/claude"
readonly LOG_DIR="$LIFE_DIR/logs"
readonly LOCK_FILE="$LOG_DIR/consolidate.lock"
readonly HEARTBEAT_FILE="$LOG_DIR/consolidate.heartbeat"
TODAY=$(date '+%Y-%m-%d')
readonly TODAY
YEAR=$(date '+%Y')
readonly YEAR
MONTH=$(date '+%m')
readonly MONTH
readonly DAILY_NOTE="$LIFE_DIR/Daily/$YEAR/$MONTH/$TODAY.md"

mkdir -p "$LOG_DIR"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [consolidate] $*" | tee -a "$LOG_DIR/consolidate.log"; }

# --- Phase 8.0.1: topology invariant ---
# ~/life/scripts must be a symlink into canonical. If someone has replaced it
# with a real directory, deploy topology has drifted and the run is unsafe.
if [[ ! -L "$LIFE_DIR/scripts" ]]; then
    log "ERROR: $LIFE_DIR/scripts is not a symlink; topology has drifted"
    log "Expected: symlink to ~/pi-cluster/life-automation/"
    exit 1
fi

# --- Phase 8.0.1: global LLM kill switch ---
# Honored by nightly (skips LLM phases) and by every LLM-calling script.
if [[ -n "${LIFE_LLM_DISABLED:-}" ]]; then
    log "LLM kill switch active (env LIFE_LLM_DISABLED=$LIFE_LLM_DISABLED); LLM phases will be skipped"
elif [[ -f "$LIFE_DIR/.llm-disabled" ]]; then
    export LIFE_LLM_DISABLED=1
    log "LLM kill switch active (sentinel $LIFE_DIR/.llm-disabled); LLM phases will be skipped"
fi

# --- Log rotation: keep last 30 days ---
find "$LOG_DIR" -name "consolidate-errors-*.json" -mtime +30 -delete 2>/dev/null || true
if [[ -f "$LOG_DIR/consolidate.log" ]] && [[ $(wc -c < "$LOG_DIR/consolidate.log") -gt 1048576 ]]; then
    mv "$LOG_DIR/consolidate.log" "$LOG_DIR/consolidate.log.old"
    log "Rotated consolidate.log (exceeded 1MB)"
fi

# --- Prevent concurrent runs ---
exec 9>"$LOCK_FILE"
flock -n 9 || { log "Already running — skipping"; exit 0; }

TMPFILE=$(mktemp /tmp/life-consolidate-XXXXXX.json)
trap 'rm -f "$TMPFILE"' EXIT

[ -f "$DAILY_NOTE" ] || { log "No daily note for $TODAY — nothing to consolidate"; exit 0; }
[ -x "$CLAUDE_BIN" ] || { log "ERROR: claude CLI not found at $CLAUDE_BIN"; exit 1; }

log "Starting consolidation for $TODAY"

# --- Ingest Maxwell dispatch results (30s timeout to prevent hanging) ---
log "Ingesting Maxwell dispatches..."
timeout 30 python3 "$LIFE_DIR/scripts/ingest_dispatches.py" 2>&1 | tee -a "$LOG_DIR/consolidate.log" || true

DAILY_CONTENT=$(cat "$DAILY_NOTE")

# --- Include Maxwell activity in consolidation ---
MAXWELL_NOTE="$LIFE_DIR/Daily/$YEAR/$MONTH/maxwell-$TODAY.md"
if [[ -f "$MAXWELL_NOTE" ]]; then
    DAILY_CONTENT="$DAILY_CONTENT

$(cat "$MAXWELL_NOTE")"
    log "Included Maxwell activity in consolidation"
fi

# --- Include Claude Code session digests ---
DIGEST_FILE="$LIFE_DIR/Daily/$YEAR/$MONTH/sessions-digest-$TODAY.jsonl"
if [[ -f "$DIGEST_FILE" ]]; then
    SESSIONS=$(python3 -c "
import json, sys
for line in open(sys.argv[1]):
    try:
        d = json.loads(line)
        files = ', '.join(d.get('files_touched', [])[:5])
        print(f\"- {d['ts'][:16]}: {d['summary']} [{files}]\")
    except (json.JSONDecodeError, KeyError):
        continue
" "$DIGEST_FILE" 2>/dev/null || true)
    if [[ -n "$SESSIONS" ]]; then
        DAILY_CONTENT="$DAILY_CONTENT

## Claude Code Sessions
$SESSIONS"
        log "Included session digests in consolidation"
    fi
fi

# Truncate if note is very long (guard against ARG_MAX)
if [ ${#DAILY_CONTENT} -gt 50000 ]; then
    DAILY_CONTENT="${DAILY_CONTENT:0:50000}
[... truncated for consolidation ...]"
    log "WARNING: daily note truncated to 50000 chars"
fi

EXISTING=$(find "$LIFE_DIR/Projects/" "$LIFE_DIR/People/" "$LIFE_DIR/Companies/" \
    -mindepth 1 -maxdepth 1 -type d -not -name '_template' -printf '%f\n' 2>/dev/null \
    | sort -u || true)

# --- Collect recent facts for contradiction detection (last 7 days, ~875 tokens) ---
RECENT_FACTS=$(python3 -c "
import json, sys
from pathlib import Path
from datetime import date, timedelta
cutoff = str(date.today() - timedelta(days=7))
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
" "$LIFE_DIR" 2>/dev/null | head -30 || true)

# Prompt with few-shot examples (reduces hallucination and format errors)
PROMPT="You are a knowledge consolidation assistant. Analyze the daily note below and output ONLY valid JSON. No markdown, no code fences, no explanation text.

TODAY: $TODAY
EXISTING ENTITIES: $EXISTING

RECENT FACTS (last 7 days — check for contradictions):
$RECENT_FACTS

DAILY NOTE:
$DAILY_CONTENT

Output this exact JSON schema (use empty arrays if nothing qualifies):
{
  \"new_entities\": [{\"type\": \"person|project|company\", \"name\": \"slug-lowercase-hyphens\", \"display\": \"Display Name\"}],
  \"fact_updates\": [{\"entity_type\": \"project|person|company|area|resource\", \"entity\": \"existing-slug\", \"date\": \"$TODAY\", \"fact\": \"one sentence\", \"category\": \"deployment|decision|configuration|lesson|preference|event|pending\", \"temporal\": false}],
  \"tacit_knowledge\": [{\"file\": \"workflow-habits|hard-rules|communication-preferences|lessons-learned\", \"entry\": \"one sentence\"}],
  \"skills\": [{\"name\": \"slug-name\", \"display\": \"How to Do X\", \"steps\": [\"step 1\", \"step 2\"]}],
  \"relationships\": [{\"from\": \"slug\", \"from_type\": \"person|project|company\", \"to\": \"slug\", \"to_type\": \"person|project|company\", \"relation\": \"works-on|owns|provides|uses|reports-to|manages|contributes-to\"}],
  \"summary\": \"one sentence\"
}

EXAMPLE INPUT:
## Log
- Fixed gateway crash on heavy. Root cause: stale PID file after power outage.
- Discussed monitoring stack with Archie — he'll own Grafana setup.
- polymarket-bot hit 3 consecutive losses. Reduced order size to \$4.00.
## Decisions
- Switch primary model to gemini-2.5-pro for complex tasks (flash for simple).
## Pending
- [ ] Set up Grafana on heavy

EXAMPLE OUTPUT:
{\"new_entities\":[{\"type\":\"person\",\"name\":\"archie\",\"display\":\"Archie\"}],\"fact_updates\":[{\"entity_type\":\"project\",\"entity\":\"pi-cluster\",\"date\":\"$TODAY\",\"fact\":\"Gateway crash caused by stale PID file after power outage — fixed\",\"category\":\"deployment\",\"temporal\":false},{\"entity_type\":\"project\",\"entity\":\"polymarket-bot\",\"date\":\"$TODAY\",\"fact\":\"Order size reduced to \$4.00 after 3 consecutive losses\",\"category\":\"configuration\",\"temporal\":true}],\"tacit_knowledge\":[{\"file\":\"workflow-habits\",\"entry\":\"After power outages, check for stale PID files in all services before restart.\"}],\"relationships\":[{\"from\":\"archie\",\"from_type\":\"person\",\"to\":\"pi-cluster\",\"to_type\":\"project\",\"relation\":\"works-on\"}],\"summary\":\"Fixed gateway crash, discussed monitoring ownership with Archie, adjusted polymarket-bot sizing.\"}

Rules:
- Entity slugs MUST be lowercase-hyphens only (e.g. \"pi-cluster\" not \"Pi-Cluster\" or \"pi_cluster\")
- new_entities: only for entities with significant discussion (3+ mentions or ongoing relationship)
- fact_updates: only for EXISTING entities listed above
- If a new fact CONTRADICTS a recent fact above, add \"supersedes\": \"exact text of old fact\" to the fact_update entry
- Only mark supersedes for genuine replacements, not additions (e.g., \"using QMD\" supersedes \"using memsearch\", but \"added Pixel\" does NOT supersede \"added Scout\")
- Set \"temporal\": true for facts about current states that will change (deploy in progress, blocked on X, current config values). Default false for permanent facts (architecture, lessons, completed events).
- tacit_knowledge: only genuinely new rules or lessons
- Return empty arrays if nothing qualifies"

log "Calling Claude for extraction (timeout 120s, model pinned)..."
if ! timeout 120 "$CLAUDE_BIN" -p "$PROMPT" \
    --output-format text \
    --no-session-persistence \
    --model claude-haiku-4-5-20251001 \
    > "$TMPFILE" 2>/dev/null; then
    log "ERROR: Claude CLI failed or timed out — skipping"
    cp "$TMPFILE" "$LOG_DIR/consolidate-errors-$TODAY.json" 2>/dev/null || true
    exit 0
fi

# --- Validate JSON before applying (reuse strip_fences from apply_extraction) ---
if ! python3 -c "
import json, sys, importlib.util
spec = importlib.util.spec_from_file_location('ae', '$LIFE_DIR/scripts/apply_extraction.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
json.loads(mod.strip_fences(open(sys.argv[1]).read()))
" "$TMPFILE" 2>/dev/null; then
    log "ERROR: Invalid JSON from Claude — saved to consolidate-errors-$TODAY.json"
    cp "$TMPFILE" "$LOG_DIR/consolidate-errors-$TODAY.json"
    exit 0
fi

log "Applying extraction..."
export CONSOLIDATION_DATE="$TODAY"
python3 "$LIFE_DIR/scripts/apply_extraction.py" < "$TMPFILE" \
    | tee -a "$LOG_DIR/consolidate.log"

# Log operation
python3 "$LIFE_DIR/scripts/log_operation.py" "consolidate" "Extraction applied for $TODAY" 2>/dev/null || true

# --- Auto-archive completed projects ---
log "Checking for completed projects to archive..."
python3 "$LIFE_DIR/scripts/auto_archive.py" 2>&1 | tee -a "$LOG_DIR/consolidate.log"

# --- Session archive ---
log "Archiving today's sessions..."
python3 "$LIFE_DIR/scripts/session_archive.py" 2>&1 | tee -a "$LOG_DIR/consolidate.log"

# --- Skill deduplication check ---
log "Checking for duplicate skills..."
python3 "$LIFE_DIR/scripts/dedup_skills.py" 2>&1 | tee -a "$LOG_DIR/consolidate.log"

# --- Fact decay ---
log "Running fact decay..."
python3 "$LIFE_DIR/scripts/decay_facts.py" 2>&1 | tee -a "$LOG_DIR/consolidate.log"

# --- Decay dashboard (Phase 4: fact health report) ---
log "Generating decay dashboard..."
python3 "$LIFE_DIR/scripts/generate_decay_dashboard.py" 2>&1 | tee -a "$LOG_DIR/consolidate.log"

# --- Summary size check ---
log "Checking summary sizes..."
python3 "$LIFE_DIR/scripts/check_summary_size.py" 2>&1 | tee -a "$LOG_DIR/consolidate.log"

# --- Heartbeat check ---
log "Running heartbeat check..."
python3 "$LIFE_DIR/scripts/heartbeat_check.py" 2>&1 | tee -a "$LOG_DIR/consolidate.log"

# >>> PHASE 8A: orphan entity detection (non-LLM, daily) >>>
# Writes logs/pending-entities.log. Does NOT honor LIFE_LLM_DISABLED —
# --orphans runs before the kill-switch check in lint_knowledge_llm.main()
# so orphan signals keep flowing during an LLM outage.
log "Running orphan entity detection..."
if ! python3 "$LIFE_DIR/scripts/lint_knowledge_llm.py" --orphans \
        2>&1 | tee -a "$LOG_DIR/consolidate.log"; then
    log "WARNING: orphan entity detection exited non-zero; continuing"
fi
# <<< PHASE 8A <<<

# --- Weekly summary (only on Sundays) ---
if [ "$(date +%u)" -eq 7 ]; then
    log "Generating weekly summary..."
    python3 "$LIFE_DIR/scripts/weekly_summary.py" 2>&1 | tee -a "$LOG_DIR/consolidate.log"

    log "Running entity enrichment (weekly)..."
    python3 "$LIFE_DIR/scripts/enrich_entities.py" 2>&1 | tee -a "$LOG_DIR/consolidate.log"

    log "Running LLM knowledge lint (weekly)..."
    python3 "$LIFE_DIR/scripts/lint_knowledge_llm.py" 2>&1 | tee -a "$LOG_DIR/consolidate.log"

    # >>> PHASE 8C: wiki rewrite (weekly, Sunday only) >>>
    # LLM-calling: honors LIFE_LLM_DISABLED internally. Wrapped in timeout
    # as a safety cap against a runaway claude CLI hang at scale. Dual tee
    # writes to both consolidate.log (for the unified view) and rewrite.log
    # (dedicated per-phase log for trivial Monday verification).
    #
    # Freshness invariant: Monday-Saturday edits to items.json are NOT
    # reflected in summaries until the following Sunday. By design
    # (cost vs freshness tradeoff).
    log "Rewriting auto-maintained entity summaries (weekly)..."
    if ! timeout 900 python3 "$LIFE_DIR/scripts/rewrite_summaries.py" \
            2>&1 | tee -a "$LOG_DIR/consolidate.log" "$LOG_DIR/rewrite.log"; then
        log "WARNING: rewrite_summaries exited non-zero or timed out; continuing"
    fi
    # <<< PHASE 8C <<<
fi

# --- Ingest raw documents (Phase 3b: Karpathy-style) ---
if ls "$LIFE_DIR/raw/"*.md "$LIFE_DIR/raw/"*.txt 2>/dev/null | head -1 > /dev/null; then
    log "Ingesting raw documents..."
    for f in "$LIFE_DIR/raw/"*.md "$LIFE_DIR/raw/"*.txt; do
        [ -f "$f" ] && timeout 120 python3 "$LIFE_DIR/scripts/ingest_raw.py" "$f" 2>&1 | tee -a "$LOG_DIR/consolidate.log"
    done
fi

# --- Maxwell FTS5 indexing (Phase 3b: unified search) ---
log "Indexing Maxwell dispatches..."
python3 "$LIFE_DIR/scripts/session_search.py" --backfill-maxwell 2>&1 | tee -a "$LOG_DIR/consolidate.log"

# --- Generate index.md (Phase 3: content catalog) ---
log "Generating knowledge base index..."
python3 "$LIFE_DIR/scripts/generate_index.py" --llm 2>&1 | tee -a "$LOG_DIR/consolidate.log"

# --- Knowledge graph (Phase 4: visualization) ---
log "Generating knowledge graph..."
python3 "$LIFE_DIR/scripts/generate_graph.py" 2>&1 | tee -a "$LOG_DIR/consolidate.log"

# --- Knowledge lint (Phase 3: consistency checks) ---
log "Running knowledge lint..."
python3 "$LIFE_DIR/scripts/lint_knowledge.py" 2>&1 | tee -a "$LOG_DIR/consolidate.log"

# --- QMD re-index (Phase 2: semantic search) ---
QMD_BIN="$HOME/.local/bin/qmd"
if [ -x "$QMD_BIN" ]; then
    log "Re-indexing QMD..."
    if timeout 120 "$QMD_BIN" update -c life 2>/dev/null; then
        timeout 300 "$QMD_BIN" embed 2>/dev/null || log "WARNING: QMD embed failed"
        log "QMD re-index complete"
    else
        log "WARNING: QMD update failed — search may be stale"
    fi
fi

# --- Git sync (push changes to GitHub) ---
if [ -d "$LIFE_DIR/.git" ]; then
    log "Syncing ~/life to git..."
    # shellcheck source=/dev/null
    for _lib in "$HOME/pi-cluster/life-automation/lib/life-git-sync.sh" \
                "$LIFE_DIR/scripts/lib/life-git-sync.sh"; do
        [ -f "$_lib" ] && { source "$_lib"; break; }
    done
    life_git_sync "$LIFE_DIR" 2>&1 | tee -a "$LOG_DIR/consolidate.log" || log "WARNING: git sync failed"
fi

# --- Weekly checks (Sundays only) ---
DOW=$(date +%u)
if [ "$DOW" = "7" ] && [ -d "$LIFE_DIR/.git" ]; then
    commit_count=$(git -C "$LIFE_DIR" rev-list --count HEAD 2>/dev/null || echo "?")
    log "$HOME/life has $commit_count commits"
    [ "${commit_count:-0}" -gt 1000 ] && log "WARNING: >1000 commits — consider squashing"
fi

# --- AGENTS.md / CLAUDE.md drift check ---
if [ -f "$HOME/AGENTS.md" ] && [ -f "$HOME/CLAUDE.md" ]; then
    if [ "$HOME/CLAUDE.md" -nt "$HOME/AGENTS.md" ]; then
        age_diff=$(( $(stat -c %Y "$HOME/CLAUDE.md") - $(stat -c %Y "$HOME/AGENTS.md") ))
        [ "$age_diff" -gt 2592000 ] && log "WARNING: AGENTS.md >30 days stale vs CLAUDE.md"
    fi
fi

# --- Healthcheck heartbeat (touch file — absence = failure) ---
touch "$HEARTBEAT_FILE"
log "Consolidation complete for $TODAY"
