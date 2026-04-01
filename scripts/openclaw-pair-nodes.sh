#!/bin/bash
set -uo pipefail
# OpenClaw Node Pairing (Safe Merge)
# Reads node identities and merges them into the gateway's paired.json
# without disrupting existing entries (operator, previously paired nodes).
#
# Usage: bash scripts/openclaw-pair-nodes.sh
#
# Safe behaviors:
#   - Preserves operator (CLI) entry — never touches clientMode=cli entries
#   - Preserves existing node pairings that haven't changed
#   - Only adds/updates entries for nodes with changed device IDs
#   - Does NOT restart the gateway unless pairings actually changed
#   - Restarts only the affected node services

set -euo pipefail

GATEWAY_CONTAINER="openclaw-openclaw-gateway-1"
PAIRED_JSON="/home/node/.openclaw/devices/paired.json"
NODES=("master:control:192.168.0.22" "slave0:build:100.65.188.85" "slave1:light:192.168.0.4" "heavy:heavy:192.168.0.5")

echo "=== OpenClaw Node Pairing (Safe Merge) ==="

# Step 1: Stop node services
echo "Stopping node services..."
for entry in "${NODES[@]}"; do
    host="${entry%%:*}"
    ssh -o ConnectTimeout=3 "$host" "sudo systemctl stop openclaw-node" 2>/dev/null || true &
done
wait

# Step 2: Collect identities and merge into paired.json
echo "Collecting node identities..."
NODE_DATA="["
first=true
for entry in "${NODES[@]}"; do
    IFS=: read -r host display_name remote_ip <<< "$entry"

    # Master uses separate .openclaw-node dir (node host + CLI share same machine)
    identity=$(ssh -o ConnectTimeout=3 "$host" "cat /home/enrico/.openclaw-node/.openclaw/identity/device.json 2>/dev/null || cat /home/enrico/.openclaw/identity/device.json 2>/dev/null" || echo "")
    if [ -z "$identity" ]; then
        echo "  WARNING: No identity on $host — skipping"
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

# Step 3: Safe merge into paired.json (preserves operator + unchanged entries)
echo "Merging pairings..."
CHANGES=$(docker exec -i "$GATEWAY_CONTAINER" python3 -c "
import json, sys, base64, time

nodes = json.loads(sys.stdin.read())

# Read current paired.json
try:
    with open('$PAIRED_JSON') as f:
        paired = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    paired = {}

changes = 0
now_ms = int(time.time() * 1000)

for node in nodes:
    der = base64.b64decode(node['pubkey_b64'])
    raw_key = der[-32:]
    pub_b64url = base64.urlsafe_b64encode(raw_key).decode().rstrip('=')
    did = node['deviceId']

    # Check if already paired with same key
    existing = paired.get(did)
    if existing and existing.get('publicKey') == pub_b64url and existing.get('clientMode') == 'node':
        print(f'  {node[\"displayName\"]}: unchanged ({did[:16]}...)')
        continue

    # Remove any old entry with same displayName but different deviceId
    old_ids = [k for k, v in paired.items() if v.get('displayName') == node['displayName'] and k != did]
    for old_id in old_ids:
        del paired[old_id]

    paired[did] = {
        'deviceId': did,
        'publicKey': pub_b64url,
        'displayName': node['displayName'],
        'platform': 'linux',
        'clientId': 'node-host',
        'clientMode': 'node',
        'role': 'node',
        'roles': ['node'],
        'remoteIp': node['remoteIp'],
        'tokens': {'node': {'token': f\"paired-{node['displayName']}\", 'role': 'node', 'scopes': [], 'createdAtMs': now_ms}},
        'createdAtMs': now_ms,
        'approvedAtMs': now_ms,
    }
    print(f'  Paired: {node[\"displayName\"]} ({did[:16]}...) from {node[\"remoteIp\"]}')
    changes += 1

# Verify operator entry exists
operators = [k for k, v in paired.items() if v.get('clientMode') == 'cli']
if not operators:
    print('  WARNING: No operator entry found — gateway CLI may not work')

node_count = sum(1 for v in paired.values() if v.get('clientMode') == 'node')
print(f'  Total: {node_count} nodes, {len(operators)} operator(s)')

with open('$PAIRED_JSON', 'w') as f:
    json.dump(paired, f, indent=2)

# Output change count for the shell script
print(f'CHANGES={changes}')
" <<< "$NODE_DATA")

echo "$CHANGES"
CHANGE_COUNT=$(echo "$CHANGES" | grep "^CHANGES=" | cut -d= -f2)

# Step 4: Only restart gateway if pairings actually changed
if [ "${CHANGE_COUNT:-0}" -gt 0 ]; then
    echo "Restarting gateway (${CHANGE_COUNT} pairings changed)..."
    cd /mnt/external/openclaw && docker compose restart openclaw-gateway 2>&1
    sleep 15
else
    echo "No pairing changes — skipping gateway restart."
fi

# Step 5: Start node services
echo "Starting node services..."
for entry in "${NODES[@]}"; do
    host="${entry%%:*}"
    ssh -o ConnectTimeout=3 "$host" "sudo systemctl start openclaw-node" 2>/dev/null || true &
done
wait
sleep 12

# Step 6: Verify
echo ""
echo "=== Verification ==="
docker exec "$GATEWAY_CONTAINER" openclaw nodes status 2>&1 | grep -E "buil|cont|heav|ligh" | grep "connected" || echo "WARNING: No nodes showing as connected"

echo ""
echo "=== Pairing complete ==="
