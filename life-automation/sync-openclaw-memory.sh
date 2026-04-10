#!/bin/bash
# sync-openclaw-memory.sh — Bridge ~/life/ daily notes into OpenClaw Maxwell gateway.
#
# Syncs a filtered daily note to the gateway's memory directory and maintains
# a managed skill-pointer block in MEMORY.md. Designed for cron (15-min via
# mini-consolidate.sh) with NFS4 write safety.
#
# Usage:
#   bash sync-openclaw-memory.sh              # normal run
#   bash sync-openclaw-memory.sh --dry-run    # show diff, no writes
#   bash sync-openclaw-memory.sh --filter-only < file.md  # filter stdin, print to stdout
set -euo pipefail

# --- Configuration (overridable via env for testing) ---
LIFE_DIR="${LIFE_DIR:-$HOME/life}"
WORKSPACE_DIR="${WORKSPACE_DIR:-/mnt/external/openclaw/workspace}"
MEMORY_DIR="${MEMORY_DIR:-$WORKSPACE_DIR/memory}"
MEMORY_MD="${MEMORY_MD:-$WORKSPACE_DIR/MEMORY.md}"
LOCK_FILE="${LOCK_FILE:-$LIFE_DIR/logs/openclaw-sync.lock}"
LOG_FILE="${LOG_FILE:-$LIFE_DIR/logs/openclaw-sync.log}"
HEALTHCHECK_FILE="${HEALTHCHECK_FILE:-$WORKSPACE_DIR/.nfs_healthcheck}"
KILL_SWITCH_FILE="${KILL_SWITCH_FILE:-/var/run/maxwell-sync.disabled}"
SYNC_SKIP_MOUNT_CHECK="${SYNC_SKIP_MOUNT_CHECK:-0}"
TODAY="${TODAY:-$(TZ=Europe/Rome date '+%F')}"

BEGIN_MARKER="<!-- BEGIN SYNC ~/life -->"
END_MARKER="<!-- END SYNC ~/life -->"

DRY_RUN=0
FILTER_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --filter-only) FILTER_ONLY=1 ;;
    esac
done

# --- Logging ---
log() {
    local msg
    msg="$(date -Is) $1"
    echo "$msg" >> "$LOG_FILE" 2>/dev/null || true
}

# --- Section filter (awk) ---
# Extracts only safe sections: Active Projects, Decisions Made, Pending Items, New Facts
# Strips YAML frontmatter. Rejects superset headers via [[:space:]]*$ anchor.
filter_daily_note() {
    local input="$1"
    # Pre-check frontmatter
    if ! head -1 "$input" | grep -q '^---'; then
        echo "WARN: no frontmatter in $input" >&2
    fi
    # Strip CRLF, then filter
    tr -d '\r' < "$input" | awk '
        /^---/ { if (++fm == 2) next; next }
        fm < 2 { next }
        /^## (Active Projects|Decisions Made|Pending Items|New Facts( Learned)?)[[:space:]]*$/ {
            print; keep = 1; next
        }
        /^## / { keep = 0; next }
        keep { print }
    '
}

# --- Filter-only mode (for testing) ---
if [ "$FILTER_ONLY" -eq 1 ]; then
    tmpfile=$(mktemp)
    cat > "$tmpfile"
    filter_daily_note "$tmpfile"
    rm -f "$tmpfile"
    exit 0
fi

# --- Kill switches ---
if [ "${LIFE_SYNC_ENABLED:-1}" = "0" ]; then
    log "SKIPPED disabled via env"
    exit 0
fi
if [ -f "$KILL_SWITCH_FILE" ]; then
    log "SKIPPED disabled via file $KILL_SWITCH_FILE"
    exit 0
fi

# --- Locking ---
mkdir -p "$(dirname "$LOCK_FILE")"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log "SKIPPED lock held"
    exit 0
fi

# --- NFS pre-flight ---
if [ "$SYNC_SKIP_MOUNT_CHECK" != "1" ]; then
    if ! mountpoint -q "$WORKSPACE_DIR" 2>/dev/null; then
        log "ERROR: $WORKSPACE_DIR not mounted"
        exit 1
    fi
    if ! timeout 5 stat "$HEALTHCHECK_FILE" >/dev/null 2>&1; then
        log "ERROR: NFS healthcheck failed ($HEALTHCHECK_FILE)"
        exit 1
    fi
fi

# --- Ensure directories exist ---
mkdir -p "$MEMORY_DIR"

# --- Clean orphaned temp files older than 20 min ---
find "$MEMORY_DIR" -name '.tmp.*' -mmin +20 -delete 2>/dev/null || true

# --- Log truncation (>100KB) ---
if [ -f "$LOG_FILE" ]; then
    log_size=$(stat -c %s "$LOG_FILE" 2>/dev/null || echo 0)
    if [ "$log_size" -gt 102400 ]; then
        tail -c 51200 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
    fi
fi

# --- Resolve daily note ---
YEAR="${TODAY:0:4}"
MONTH="${TODAY:5:2}"
DAILY_NOTE="$LIFE_DIR/Daily/$YEAR/$MONTH/$TODAY.md"

# Retry once if missing (may be mid-write)
if [ ! -f "$DAILY_NOTE" ]; then
    sleep 1
    if [ ! -f "$DAILY_NOTE" ]; then
        DAILY_NOTE_MISSING=1
    fi
fi
DAILY_NOTE_MISSING="${DAILY_NOTE_MISSING:-0}"

# --- Filter daily note ---
if [ "$DAILY_NOTE_MISSING" -eq 1 ]; then
    filtered_content="<!-- No daily note found for $TODAY -->"
else
    filtered_content=$(filter_daily_note "$DAILY_NOTE")
    if [ -z "$filtered_content" ]; then
        filtered_content="<!-- Daily note for $TODAY had no extractable sections -->"
    fi
fi

# --- Content-derived canary ---
# IMPORTANT: always use printf '%s', never <<< (<<< adds trailing newline)
canary_hash=$(printf '%s' "$filtered_content" | sha256sum | cut -c1-12)

# --- Build intended daily note output ---
intended_daily="${filtered_content}
<!-- canary:${canary_hash} -->"

# --- Build intended managed block ---
managed_block_content="<!-- DO NOT EDIT inside this block — maintained by sync-openclaw-memory.sh -->

## ~/life/ Knowledge Base (live, via qmd skill)

Your live knowledge base lives at \`~/life/\` on the host — a PARA-structured
markdown vault indexed in the \`qmd\` skill's \`life\` collection.
Today's daily note (filtered) is mirrored at \`memory/$TODAY.md\`.

**Reading current state:**
  - \`memory/$TODAY.md\` for today's activity (synced from ~/life/Daily/)
  - \`qmd search \"X\"\` (BM25) / \`qmd vsearch \"X\"\` (semantic) / \`qmd get <path>\`

**Dispatching work to cluster agents:**
  - Use the \`mission-control\` skill: \`mc dispatch <persona> \"prompt\"\`
  - IMPORTANT: Before dispatching, you MUST first create a plan or PRD
    describing what work needs to be done, why, and acceptance criteria.
    Only dispatch after the plan is reviewed or acknowledged.
  - Other MC commands: \`mc tasks\`, \`mc task <id>\`, \`mc create\`, \`mc update\`

Canonical ~/life/ paths:
  - Daily notes:     Daily/YYYY/MM/YYYY-MM-DD.md
  - Active projects: Projects/*/summary.md
  - Entity graph:    relationships.json

<!-- canary:${canary_hash} -->"

intended_memory_block="${BEGIN_MARKER}
${managed_block_content}
${END_MARKER}"

# --- Compute hashes for idempotency (dual-hash: check both files independently) ---
# Hash the content as it will appear on disk (printf '%s\n' adds trailing newline)
intended_daily_hash=$(printf '%s\n' "$intended_daily" | sha256sum | cut -c1-64)

current_daily_hash=""
if [ -f "$MEMORY_DIR/$TODAY.md" ]; then
    current_daily_hash=$(sha256sum < "$MEMORY_DIR/$TODAY.md" | cut -c1-64)
fi

daily_needs_update=true
memory_needs_update=true

[ "$intended_daily_hash" = "$current_daily_hash" ] && daily_needs_update=false

# For MEMORY.md, we need to check if the managed block matches
# (we compare the full file hash after building the intended full file)
compute_memory_needs_update() {
    if [ ! -f "$MEMORY_MD" ]; then
        memory_needs_update=true
        return
    fi
    local current
    current=$(tr -d '\r' < "$MEMORY_MD")
    local begin_count end_count
    begin_count=$(printf '%s\n' "$current" | grep -cxF "$BEGIN_MARKER" || true)
    end_count=$(printf '%s\n' "$current" | grep -cxF "$END_MARKER" || true)

    if [ "$begin_count" -ne "$end_count" ]; then
        log "ERROR: marker count mismatch (BEGIN=$begin_count, END=$end_count)"
        exit 1
    fi
    if [ "$begin_count" -gt 1 ]; then
        log "ERROR: duplicate markers (BEGIN=$begin_count, END=$end_count)"
        exit 1
    fi

    # Build what the full file WOULD look like
    local intended_full
    if [ "$begin_count" -eq 0 ]; then
        if [ -z "$current" ]; then
            intended_full="$intended_memory_block"
        else
            intended_full="${current}

${intended_memory_block}"
        fi
    else
        # Use awk instead of sed to avoid escaping issues with ~/life in markers
        local before after
        before=$(printf '%s\n' "$current" | awk -v m="$BEGIN_MARKER" '$0 == m { exit } { print }')
        after=$(printf '%s\n' "$current" | awk -v m="$END_MARKER" 'found { print } $0 == m { found=1 }')
        intended_full=""
        [ -n "$before" ] && intended_full="${before}"
        if [ -n "$intended_full" ]; then
            intended_full="${intended_full}
${intended_memory_block}"
        else
            intended_full="$intended_memory_block"
        fi
        [ -n "$after" ] && intended_full="${intended_full}
${after}"
    fi

    local intended_full_hash current_full_hash
    intended_full_hash=$(printf '%s' "$intended_full" | sha256sum | cut -c1-64)
    current_full_hash=$(printf '%s' "$current" | sha256sum | cut -c1-64)

    if [ "$intended_full_hash" = "$current_full_hash" ]; then
        memory_needs_update=false
    else
        # Store for later use
        INTENDED_FULL_MEMORY="$intended_full"
    fi
}

compute_memory_needs_update

# --- Skip if both are up to date ---
if [ "$daily_needs_update" = false ] && [ "$memory_needs_update" = false ]; then
    log "SKIPPED hash match"
    exit 0
fi

# --- Dry-run mode ---
if [ "$DRY_RUN" -eq 1 ]; then
    log "DRY-RUN would update: daily=$daily_needs_update memory=$memory_needs_update"
    if [ "$daily_needs_update" = true ]; then
        echo "DRY-RUN: would write $MEMORY_DIR/$TODAY.md"
        echo "$intended_daily"
    fi
    if [ "$memory_needs_update" = true ]; then
        echo "DRY-RUN: would update $MEMORY_MD"
    fi
    exit 0
fi

# --- Atomic write helper ---
atomic_write() {
    local target="$1"
    local content="$2"
    local dir
    dir=$(dirname "$target")
    local tmpfile
    tmpfile=$(mktemp "$dir/.tmp.XXXXXX")
    printf '%s\n' "$content" > "$tmpfile"
    sync
    # Preserve permissions if target exists
    if [ -f "$target" ]; then
        chmod --reference="$target" "$tmpfile" 2>/dev/null || true
    fi
    mv -f "$tmpfile" "$target"
    sync
}

# --- Write MEMORY.md first (higher-value file) ---
if [ "$memory_needs_update" = true ]; then
    if [ ! -f "$MEMORY_MD" ]; then
        # File doesn't exist — create with just the block
        atomic_write "$MEMORY_MD" "$intended_memory_block"
    else
        # Use the pre-computed intended full content
        atomic_write "$MEMORY_MD" "$INTENDED_FULL_MEMORY"
    fi
fi

# --- Write daily note ---
if [ "$daily_needs_update" = true ]; then
    atomic_write "$MEMORY_DIR/$TODAY.md" "$intended_daily"
fi

# --- Post-write verification ---
if [ "$daily_needs_update" = true ] && [ -f "$MEMORY_DIR/$TODAY.md" ]; then
    sync
    verify_hash=$(sha256sum < "$MEMORY_DIR/$TODAY.md" | cut -c1-64)
    # The file has a trailing newline from printf '%s\n', so hash the intended content + newline
    intended_with_newline_hash=$(printf '%s\n' "$intended_daily" | sha256sum | cut -c1-64)
    if [ "$verify_hash" != "$intended_with_newline_hash" ]; then
        log "ERROR: post-write verification failed for daily note"
        exit 1
    fi
fi

# --- Cleanup: remove daily note files older than 14 days ---
find "$MEMORY_DIR" -name '*.md' -mtime +14 -not -name '.tmp.*' -delete 2>/dev/null || true

# --- Log success ---
log "SYNCED daily=$daily_needs_update memory=$memory_needs_update canary=$canary_hash"
exit 0
