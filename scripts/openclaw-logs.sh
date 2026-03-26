#!/bin/bash
set -uo pipefail
# Cluster-wide log viewer
# Usage: openclaw-logs.sh [LINES]    (default: 50)
LINES=${1:-50}

echo "=== Heavy: service logs ==="
tail -n "$LINES" /var/log/openclaw-*.log 2>/dev/null
echo ""

echo "=== Heavy: Docker (gateway) ==="
docker compose -f /mnt/external/openclaw/docker-compose.yml logs --tail="$LINES" --no-color 2>/dev/null | tail -n "$LINES"
echo ""

echo "=== Heavy: journal ==="
journalctl -u 'openclaw-*' --no-pager -n "$LINES" 2>/dev/null
echo ""

for host in master slave0 slave1; do
    echo "=== $host: journal ==="
    ssh -o ConnectTimeout=3 -o BatchMode=yes "$host" "journalctl -u 'openclaw-*' --no-pager -n $LINES" 2>/dev/null
    echo ""
done
