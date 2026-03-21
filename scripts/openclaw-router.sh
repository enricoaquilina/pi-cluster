#!/bin/bash
# OpenClaw Intelligent Task Router
# Selects the best node for a given task type based on cached node health and role affinity.
#
# Usage:
#   openclaw-router.sh <task_type>
#   openclaw-router.sh coding     → returns best node for coding tasks
#   openclaw-router.sh research   → returns best node for research tasks
#   openclaw-router.sh compute    → returns best node for heavy compute
#   openclaw-router.sh any        → returns least-loaded node
#
# Reads from /tmp/openclaw-node-stats.json (written by openclaw-stats-collector.sh every 30s)
# Falls back to live collection if cache is stale (>120s old).
#
# Output: node name (e.g., "build", "light", "heavy") or "none"
# Exit codes: 0 = node selected, 1 = no suitable node

set -euo pipefail

TASK_TYPE="${1:-any}"
CACHE_FILE="/tmp/openclaw-node-stats.json"
CACHE_MAX_AGE=120
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Refresh cache if stale or missing
if [ ! -f "$CACHE_FILE" ] || [ "$(( $(date +%s) - $(stat -c %Y "$CACHE_FILE") ))" -gt "$CACHE_MAX_AGE" ]; then
    bash "$SCRIPT_DIR/openclaw-stats-collector.sh" 2>/dev/null
fi

# Route using cached stats (sub-millisecond)
best_node=$(python3 -c "
import json

task_type = '$TASK_TYPE'
max_ram = {'build': 85, 'light': 80, 'heavy': 90}
affinity = {
    'coding': ['build', 'heavy', 'light'],
    'research': ['light', 'build', 'heavy'],
    'compute': ['heavy', 'build', 'light'],
    'any': ['heavy', 'build', 'light'],
}
roles = {'build': 'coding', 'light': 'research', 'heavy': 'compute'}

try:
    with open('$CACHE_FILE') as f:
        data = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    print('none')
    exit()

candidates = affinity.get(task_type, affinity['any'])
best = None
best_score = 999999

for node in data.get('nodes', []):
    name = node['name']
    if name not in candidates:
        continue
    if not node.get('reachable') or not node.get('connected'):
        continue
    ram_pct = node.get('ram_pct', 100)
    if ram_pct > max_ram.get(name, 85):
        continue

    load = node.get('load', 99)
    score = ram_pct + int(load) * 10
    if roles.get(name) == task_type:
        score -= 50

    if score < best_score:
        best_score = score
        best = name

print(best or 'none')
")

echo "$best_node"
[ "$best_node" != "none" ]
