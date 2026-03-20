#!/bin/bash
# OpenClaw Intelligent Task Router
# Selects the best node for a given task type based on node health and role affinity.
#
# Usage:
#   openclaw-router.sh <task_type>
#   openclaw-router.sh coding     → returns best node for coding tasks
#   openclaw-router.sh research   → returns best node for research tasks
#   openclaw-router.sh compute    → returns best node for heavy compute
#   openclaw-router.sh any        → returns least-loaded node
#
# Output: node name (e.g., "build", "light", "heavy") or "none" if all nodes are overloaded
#
# Exit codes:
#   0 = node selected
#   1 = no suitable node found

set -euo pipefail

TASK_TYPE="${1:-any}"
RAM_THRESHOLD=85
CONNECT_TIMEOUT=3

# Node definitions: name:ssh_host:role:max_ram_pct
NODES=(
    "build:slave0:coding:85"
    "light:slave1:research:80"
    "heavy:heavy:compute:90"
)

# Role affinity: which nodes prefer which task types (ordered by preference)
declare -A ROLE_AFFINITY
ROLE_AFFINITY[coding]="build heavy light"
ROLE_AFFINITY[research]="light build heavy"
ROLE_AFFINITY[compute]="heavy build light"
ROLE_AFFINITY[any]="heavy build light"

get_node_stats() {
    local ssh_host="$1"
    # Returns: ram_pct load_1m available_mb connected
    local stats
    stats=$(ssh -o ConnectTimeout="$CONNECT_TIMEOUT" -o BatchMode=yes "$ssh_host" '
        RAM_PCT=$(free | awk "/^Mem:/ {printf \"%.0f\", \$3/\$2 * 100}")
        LOAD=$(cat /proc/loadavg | cut -d" " -f1)
        AVAIL_MB=$(free -m | awk "/^Mem:/ {print \$7}")
        echo "$RAM_PCT $LOAD $AVAIL_MB"
    ' 2>/dev/null) || echo ""

    if [ -z "$stats" ]; then
        echo "unreachable"
        return 1
    fi
    echo "$stats"
}

# Cache the connected nodes list (one CLI call instead of per-node)
CONNECTED_NODES=$(docker exec openclaw-openclaw-gateway-1 openclaw nodes status 2>&1 | grep "connected" | grep -oP '^\│\s*\K\S+' | tr -d '│ ' || echo "")

check_node_connected() {
    local node_name="$1"
    # Match partial name (table truncates "heavy" to "heav")
    echo "$CONNECTED_NODES" | grep -q "${node_name:0:4}"
}

# Determine candidate order based on task type
candidates="${ROLE_AFFINITY[$TASK_TYPE]:-${ROLE_AFFINITY[any]}}"

best_node=""
best_score=999999

for candidate in $candidates; do
    # Find node config
    for node_def in "${NODES[@]}"; do
        IFS=: read -r name ssh_host role max_ram <<< "$node_def"
        if [ "$name" != "$candidate" ]; then
            continue
        fi

        # Check if node is connected to gateway
        if ! check_node_connected "$name" 2>/dev/null; then
            continue
        fi

        # Get current stats
        stats=$(get_node_stats "$ssh_host")
        if [ "$stats" = "unreachable" ]; then
            continue
        fi

        read -r ram_pct load avail_mb <<< "$stats"

        # Skip if over threshold
        if [ "${ram_pct:-100}" -gt "${max_ram:-85}" ]; then
            continue
        fi

        # Score: lower is better
        # RAM usage (weight 1) + load penalty
        # Strong role affinity: -50 for exact match, ensures preferred node wins
        # unless it's significantly more loaded
        score=$((ram_pct))
        load_int=$(echo "$load" | cut -d. -f1)
        score=$((score + load_int * 10))

        if [ "$role" = "$TASK_TYPE" ]; then
            score=$((score - 50))
        fi

        if [ "$score" -lt "$best_score" ]; then
            best_score="$score"
            best_node="$name"
        fi

        break
    done
done

if [ -z "$best_node" ]; then
    echo "none"
    exit 1
fi

echo "$best_node"
