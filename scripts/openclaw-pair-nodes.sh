#!/bin/bash
# Auto-pair OpenClaw node hosts with the gateway
# Usage: bash scripts/openclaw-pair-nodes.sh
#
# This script reads node identity files from slave nodes,
# injects them into the gateway's paired.json, and restarts
# everything in the correct order.
#
# The OpenClaw gateway requires manual pairing approval for LAN
# connections, but the CLI startup overhead (~6s) makes it impossible
# to catch the brief pending window. This script bypasses the issue
# by directly writing to paired.json.

set -euo pipefail

GATEWAY_CONTAINER="openclaw-openclaw-gateway-1"
PAIRED_JSON="/home/node/.openclaw/devices/paired.json"
NODES=("slave0:build:192.168.0.3" "slave1:light:192.168.0.4")

echo "=== OpenClaw Node Pairing ==="

# Step 1: Stop node services
echo "Stopping node services..."
for entry in "${NODES[@]}"; do
    host="${entry%%:*}"
    ssh "$host" "sudo systemctl stop openclaw-node" 2>/dev/null || true &
done
wait

# Step 2: Collect node identities
echo "Collecting node identities..."
INJECT_SCRIPT="import json, sys, base64, time
paired_path = '$PAIRED_JSON'
with open(paired_path) as f:
    paired = json.load(f)

# Remove existing node entries
paired = {k:v for k,v in paired.items() if v.get('clientMode') != 'node'}

now_ms = int(time.time() * 1000)
nodes = json.loads(sys.stdin.read())

for node in nodes:
    der = base64.b64decode(node['pubkey_b64'])
    raw_key = der[-32:]
    pub_b64url = base64.urlsafe_b64encode(raw_key).decode().rstrip('=')

    paired[node['deviceId']] = {
        'deviceId': node['deviceId'],
        'publicKey': pub_b64url,
        'displayName': node['displayName'],
        'platform': 'linux',
        'clientId': 'node-host',
        'clientMode': 'node',
        'role': 'node',
        'roles': ['node'],
        'remoteIp': node['remoteIp'],
        'tokens': {
            'node': {
                'token': f\"auto-paired-{node['displayName']}\",
                'role': 'node',
                'scopes': [],
                'createdAtMs': now_ms
            }
        },
        'createdAtMs': now_ms,
        'approvedAtMs': now_ms
    }
    print(f\"  Paired: {node['displayName']} ({node['deviceId'][:16]}...) from {node['remoteIp']}\")

with open(paired_path, 'w') as f:
    json.dump(paired, f, indent=2)

node_count = sum(1 for v in paired.values() if v.get('clientMode') == 'node')
print(f\"  Total paired nodes: {node_count}\")
"

# Collect identity data from each node
NODE_DATA="["
first=true
for entry in "${NODES[@]}"; do
    IFS=: read -r host display_name remote_ip <<< "$entry"

    identity=$(ssh "$host" "cat /home/enrico/.openclaw/identity/device.json 2>/dev/null" || echo "")
    if [ -z "$identity" ]; then
        echo "  WARNING: No identity on $host — node may need to run once first"
        continue
    fi

    device_id=$(echo "$identity" | python3 -c "import json,sys; print(json.load(sys.stdin)['deviceId'])")
    pubkey_pem=$(echo "$identity" | python3 -c "import json,sys; print(json.load(sys.stdin)['publicKeyPem'])")
    pubkey_b64=$(echo "$pubkey_pem" | python3 -c "
import sys
lines = [l.strip() for l in sys.stdin if not l.startswith('---')]
print(''.join(lines))
")

    if [ "$first" = true ]; then first=false; else NODE_DATA+=","; fi
    NODE_DATA+="{\"deviceId\":\"$device_id\",\"displayName\":\"$display_name\",\"remoteIp\":\"$remote_ip\",\"pubkey_b64\":\"$pubkey_b64\"}"
done
NODE_DATA+="]"

# Step 3: Inject into gateway
echo "Injecting pairings into gateway..."
echo "$NODE_DATA" | docker exec -i "$GATEWAY_CONTAINER" python3 -c "$INJECT_SCRIPT"

# Step 4: Restart gateway to load new pairings
echo "Restarting gateway..."
cd /mnt/external/openclaw && docker compose restart openclaw-gateway 2>&1
sleep 15

# Step 5: Start node services
echo "Starting node services..."
for entry in "${NODES[@]}"; do
    host="${entry%%:*}"
    ssh "$host" "sudo systemctl start openclaw-node" &
done
wait
sleep 12

# Step 6: Verify
echo ""
echo "=== Verification ==="
docker exec "$GATEWAY_CONTAINER" openclaw nodes status 2>&1 | grep -E "build|light|paired|connected" || echo "WARNING: No nodes showing as connected"

echo ""
echo "=== Pairing complete ==="
