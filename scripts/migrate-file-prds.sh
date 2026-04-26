#!/usr/bin/env bash
# One-time migration: load file-based PRDs into MC database.
# Run after PR #186 is merged and v3 migration has been applied.
#
# Usage: API_KEY=<key> ./scripts/migrate-file-prds.sh

set -euo pipefail

MC_URL="${MISSION_CONTROL_URL:-http://192.168.0.5:8000}"
API_KEY="${API_KEY:?Set API_KEY}"

migrated=0
for prd in ~/life/Projects/*/prd.md; do
    [ -f "$prd" ] || continue
    dir=$(dirname "$prd")
    slug=$(basename "$dir" | tr 'A-Z ' 'a-z-')
    title=$(head -20 "$prd" | grep -m1 "^# " | sed 's/^# PRD: //' | sed 's/^# //')
    [ -z "$title" ] && title="$slug"

    echo -n "Migrating $slug ... "
    content=$(jq -Rs . < "$prd")
    curl -sf -X POST "${MC_URL}/api/prd" \
        -H "X-Api-Key: ${API_KEY}" \
        -H "Content-Type: application/json" \
        -d "{\"slug\":\"${slug}\",\"title\":$(jq -Rn --arg t "$title" '$t'),\"content\":${content}}" \
        | jq -r '.status // "created"'
    migrated=$((migrated + 1))
done

echo "Done. Migrated ${migrated} PRD(s)."
