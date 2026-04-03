#!/bin/bash
# Verifies init.sh created the expected structure. Run after init.sh.
set -euo pipefail

FAIL=0
check() {
    local desc="$1" path="$2"
    [[ -e "$path" ]] && echo "PASS: $desc" || { echo "FAIL: $desc — missing: $path"; FAIL=1; }
}
check_contains() {
    local desc="$1" file="$2" pattern="$3"
    grep -qi "$pattern" "$file" 2>/dev/null && echo "PASS: $desc" \
        || { echo "FAIL: $desc — '$pattern' not found in $file"; FAIL=1; }
}
check_json() {
    local desc="$1" file="$2"
    python3 -c "import json; json.load(open('$file'))" 2>/dev/null \
        && echo "PASS: $desc" || { echo "FAIL: $desc — invalid JSON: $file"; FAIL=1; }
}

L="$HOME/life"

# Directories
check "Projects dir"           "$L/Projects"
check "Areas/about-me dir"     "$L/Areas/about-me"
check "Resources dir"          "$L/Resources"
check "Archives dir"           "$L/Archives"
check "People dir"             "$L/People"
check "Companies dir"          "$L/Companies"
check "Daily dir"              "$L/Daily"
check "logs dir"               "$L/logs"
check "scripts dir"            "$L/scripts"
check "scripts/tests dir"      "$L/scripts/tests"

# Project dirs
check "pi-cluster dir"         "$L/Projects/pi-cluster"
check "openclaw-maxwell dir"   "$L/Projects/openclaw-maxwell"
check "polymarket-bot dir"     "$L/Projects/polymarket-bot"
check "cloudflare resource"    "$L/Resources/cloudflare"

# Forte rule: NO empty pre-created dirs
check_absent() {
    local desc="$1" path="$2"
    [[ ! -e "$path" ]] && echo "PASS: $desc (not pre-created)" \
        || { echo "FAIL: $desc — should not pre-create empty dir: $path"; FAIL=1; }
}
check_absent "finances not pre-created" "$L/Areas/finances"
check_absent "health not pre-created"   "$L/Areas/health"
check_absent "home not pre-created"     "$L/Areas/home"

# about-me files
check "profile.md"             "$L/Areas/about-me/profile.md"
check "hard-rules.md"          "$L/Areas/about-me/hard-rules.md"
check "workflow-habits.md"     "$L/Areas/about-me/workflow-habits.md"
check "communication-prefs.md" "$L/Areas/about-me/communication-preferences.md"
check "lessons-learned.md"     "$L/Areas/about-me/lessons-learned.md"

# Scripts
check "apply_extraction.py"     "$L/scripts/apply_extraction.py"
check "nightly-consolidate.sh"  "$L/scripts/nightly-consolidate.sh"
check "test_apply_extraction.py" "$L/scripts/tests/test_apply_extraction.py"

# CLAUDE.md at home root (NOT inside ~/life/)
check "CLAUDE.md at ~/"        "$HOME/CLAUDE.md"

# Today's daily note (YYYY/MM hierarchy)
TODAY=$(date +%Y-%m-%d)
YEAR=$(date +%Y); MONTH=$(date +%m)
check "today's daily note"     "$L/Daily/$YEAR/$MONTH/$TODAY.md"

# Post-migration: entity files
check "pi-cluster summary.md"  "$L/Projects/pi-cluster/summary.md"
check "pi-cluster items.json"  "$L/Projects/pi-cluster/items.json"
check "cloudflare summary.md"  "$L/Resources/cloudflare/summary.md"

# JSON validity
check_json "pi-cluster items.json is valid JSON" "$L/Projects/pi-cluster/items.json"

# Content checks
check_contains "hard-rules has email rule"     "$L/Areas/about-me/hard-rules.md"     "email is never"
check_contains "hard-rules has approval rule"  "$L/Areas/about-me/hard-rules.md"     "explicit approval"
check_contains "workflow-habits has staged"    "$L/Areas/about-me/workflow-habits.md" "staged"
check_contains "CLAUDE.md loads hard-rules"    "$HOME/CLAUDE.md"                     "hard-rules"
check_contains "CLAUDE.md has session protocol" "$HOME/CLAUDE.md"                   "Session Protocol"
check_contains "pi-cluster summary has nodes"  "$L/Projects/pi-cluster/summary.md"  "master\|192.168"
check_contains "MEMORY.md has redirect"        "$HOME/.claude/projects/-home-enrico/memory/MEMORY.md" "MIGRATED"

# items.json has at least one entry
ITEM_COUNT=$(python3 -c "import json; print(len(json.load(open('$L/Projects/pi-cluster/items.json'))))")
[[ "$ITEM_COUNT" -gt 0 ]] && echo "PASS: pi-cluster items.json has $ITEM_COUNT entries" \
    || { echo "FAIL: pi-cluster items.json is empty"; FAIL=1; }

echo ""
[[ "$FAIL" -eq 0 ]] && echo "All tests passed." || { echo "Some tests FAILED ($FAIL)."; exit 1; }
