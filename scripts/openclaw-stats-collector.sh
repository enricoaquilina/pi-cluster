#!/bin/bash
# OpenClaw Stats Collector (lightweight)
# Updates connection status from gateway and pushes to Mission Control.
# Node stats now arrive via push from openclaw-node-agent.py on each node.
#
# Usage: runs via systemd timer every 30 seconds
#   bash scripts/openclaw-stats-collector.sh

set -uo pipefail

CACHE_FILE="/tmp/openclaw-node-stats.json"
GATEWAY_CONTAINER="openclaw-openclaw-gateway-1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Update connection status — try gateway CLI first, fall back to healthcheck
connected_raw=$(docker exec "$GATEWAY_CONTAINER" timeout 8 openclaw nodes status 2>&1 | grep "paired.*connected" | grep -v "disconnected" | grep -oP '^\│\s*\K\S+' | tr -d '│ ' 2>/dev/null || echo "")

# Fallback: if CLI fails, infer connection from node agent push freshness
if [ -z "$connected_raw" ] && [ -f "$CACHE_FILE" ]; then
    connected_raw=$(python3 -c "
import json, time
with open('$CACHE_FILE') as f:
    data = json.load(f)
now = time.time()
for n in data.get('nodes', []):
    ts = n.get('push_ts', 0)
    if now - ts < 90:  # pushed within 90s = connected
        print(n['name'])
" 2>/dev/null || echo "")
fi

if [ -f "$CACHE_FILE" ]; then
    export CONNECTED_RAW="$connected_raw"
    python3 -c "
import json, os

with open('$CACHE_FILE') as f:
    data = json.load(f)

connected = set(os.environ.get('CONNECTED_RAW', '').split())

for n in data.get('nodes', []):
    name = n['name']
    # Match partial name (gateway truncates: build→buil, control→cont)
    n['connected'] = any(name.startswith(c) or c.startswith(name[:4]) for c in connected)

with open('$CACHE_FILE', 'w') as f:
    json.dump(data, f)
" 2>/dev/null
fi

# Feed Mission Control dashboard
python3 "$SCRIPT_DIR/openclaw-mc-feed.py" 2>/dev/null || true
