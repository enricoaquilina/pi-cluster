#!/bin/bash
# OpenClaw E2E Cluster Test Suite
# Validates the entire cluster is functioning correctly.
# Runs as: make openclaw-test  or  daily cron
#
# Tests:
#   1. SSH connectivity to all nodes
#   2. Node agent stats pushing
#   3. Gateway: all nodes paired + connected
#   4. Dispatch: execute on each node
#   5. Python interpreter on each node
#   6. NFS read/write on each node
#   7. Router returns correct node per task type
#   8. Health API returns valid data
#   9. Mission Control API has fresh data
#  10. Budget API returns spend data
#  11. Disk space on all nodes (<90%)
#  12. Backup freshness (<25h) and size (>100K)
#  13. MC/Router API reachable from master (cross-node connectivity)
#  14. WhatsApp provider active
#  15. Gateway restart recovery (--full only)
#  21. Workspace write permissions (NFS ownership)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$SCRIPT_DIR/.env.cluster" ] && source "$SCRIPT_DIR/.env.cluster"
# shellcheck source=scripts/lib/test-harness.sh
source "$SCRIPT_DIR/lib/test-harness.sh"
# shellcheck source=scripts/lib/telegram.sh
source "$SCRIPT_DIR/lib/telegram.sh" 2>/dev/null || send_telegram() { :; }
# shellcheck source=scripts/lib/ssh.sh
source "$SCRIPT_DIR/lib/ssh.sh"
API="http://127.0.0.1:8520"
MC_API="${MC_API_URL:-http://192.168.0.5:8000/api}"
GATEWAY="openclaw-openclaw-gateway-1"

# Nodes: name:ssh_host
NODES=("control:master" "build:slave0" "light:slave1" "heavy:heavy")

TELEGRAM_MODE=false
[ "${1:-}" = "--telegram" ] && TELEGRAM_MODE=true

echo "=== OpenClaw E2E Test Suite ==="
date
echo ""

# ── Test 1: SSH connectivity ─────────────────────────────────────────────────
echo "1. SSH connectivity"
for entry in "${NODES[@]}"; do
    IFS=: read -r name ssh_host <<< "$entry"
    if cluster_ssh "$ssh_host" "echo ok" > /dev/null 2>&1; then
        pass "SSH $name"
    else
        fail "SSH $name" "unreachable"
    fi
done

# ── Test 2: Node agent stats ─────────────────────────────────────────────────
echo "2. Node agent stats"
stats=$(curl -sf "$API/nodes" 2>/dev/null)
if [ -n "$stats" ]; then
    cache_age=$(echo "$stats" | python3 -c "import json,sys; print(json.load(sys.stdin).get('cache_age_seconds',999))" 2>/dev/null)
    if [ "${cache_age%.*}" -lt 120 ] 2>/dev/null; then
        pass "Stats cache fresh (${cache_age}s)"
    else
        fail "Stats cache stale" "${cache_age}s old"
    fi

    node_count=$(echo "$stats" | python3 -c "import json,sys; print(len([n for n in json.load(sys.stdin).get('nodes',[]) if n.get('reachable')]))" 2>/dev/null)
    if [ "$node_count" -eq 4 ]; then
        pass "All 4 nodes reporting stats"
    else
        fail "Node stats" "only $node_count/4 nodes reporting"
    fi
else
    fail "Stats API" "unreachable"
fi

# ── Test 2b: Push freshness ──────────────────────────────────────────────────
if [ -n "$stats" ]; then
    stale_pushes=$(echo "$stats" | python3 -c "
import json, sys, time
d = json.load(sys.stdin)
now = time.time()
stale = [n['name'] for n in d.get('nodes', []) if now - n.get('push_ts', 0) > 120]
print(','.join(stale) if stale else '')
" 2>/dev/null)
    [ -z "$stale_pushes" ] && pass "All node agent pushes fresh (<120s)" || \
        fail "Node agent push stale" "$stale_pushes (check UFW port 8520)"
fi

# ── Test 3: Gateway connectivity ─────────────────────────────────────────────
echo "3. Gateway connectivity"
gw_status=$(docker ps --filter "name=$GATEWAY" --format '{{.Status}}' 2>/dev/null)
if echo "$gw_status" | grep -q "healthy"; then
    pass "Gateway container healthy"
else
    fail "Gateway container" "$gw_status"
fi

connected=$(docker exec "$GATEWAY" sh -c 'OPENCLAW_GATEWAY_TOKEN=$OPENCLAW_GATEWAY_TOKEN timeout 10 node dist/index.js nodes status 2>&1' | grep "paired.*connected" | grep -vc "disconnected")
if [ "$connected" -eq 4 ]; then
    pass "All 4 nodes connected to gateway"
else
    fail "Gateway nodes" "only $connected/4 connected"
fi

# ── Test 4: Dispatch to each node ────────────────────────────────────────────
echo "4. Dispatch execution"
for entry in "${NODES[@]}"; do
    IFS=: read -r name _ <<< "$entry"
    result=$(docker exec "$GATEWAY" sh -c "OPENCLAW_GATEWAY_TOKEN=$OPENCLAW_GATEWAY_TOKEN timeout 15 node dist/index.js nodes run --node $name --raw 'echo E2E_OK_$name'" 2>&1)
    if echo "$result" | grep -q "E2E_OK_$name"; then
        pass "Dispatch $name"
    else
        fail "Dispatch $name" "command failed"
    fi
done

# ── Test 5: Python interpreter ───────────────────────────────────────────────
echo "5. Python interpreter"
for entry in "${NODES[@]}"; do
    IFS=: read -r name _ <<< "$entry"
    result=$(docker exec "$GATEWAY" sh -c "OPENCLAW_GATEWAY_TOKEN=$OPENCLAW_GATEWAY_TOKEN timeout 15 node dist/index.js nodes run --node $name --raw 'bash -c \"python3 -c \\\"print(\\\\\\\"PY_OK\\\\\\\")\\\"\"'" 2>&1)
    if echo "$result" | grep -q "PY_OK"; then
        pass "Python $name"
    else
        fail "Python $name" "interpreter blocked or failed"
    fi
done

# ── Test 6: NFS read/write ───────────────────────────────────────────────────
echo "6. NFS read/write"
for entry in "${NODES[@]}"; do
    IFS=: read -r name ssh_host <<< "$entry"
    test_file="/opt/workspace/scripts/.e2e-test-$name"
    result=$(cluster_ssh "$ssh_host" \
        "echo 'e2e_test' > $test_file && cat $test_file && rm -f $test_file" 2>/dev/null)
    if [ "$result" = "e2e_test" ]; then
        pass "NFS $name"
    else
        fail "NFS $name" "read/write failed"
    fi
done

# ── Test 7: Router affinity ──────────────────────────────────────────────────
echo "7. Router affinity"
declare -A EXPECTED_ROUTES
EXPECTED_ROUTES[coding]=build
EXPECTED_ROUTES[research]=light
EXPECTED_ROUTES[compute]=heavy
EXPECTED_ROUTES[orchestrator]=control

for task_type in coding research compute orchestrator; do
    result=$(curl -sf "$API/route/$task_type" 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get('node',''))" 2>/dev/null)
    expected="${EXPECTED_ROUTES[$task_type]}"
    if [ "$result" = "$expected" ]; then
        pass "Route $task_type → $result"
    else
        fail "Route $task_type" "got '$result', expected '$expected'"
    fi
done

# ── Test 8: Health API ───────────────────────────────────────────────────────
echo "8. Health API"
health=$(curl -sf "$API/health" 2>/dev/null)
if [ -n "$health" ]; then
    pass "Health API responds"
else
    fail "Health API" "no response"
fi

concurrency=$(curl -sf "$API/concurrency" 2>/dev/null)
if echo "$concurrency" | grep -q "limits"; then
    pass "Concurrency API responds"
else
    fail "Concurrency API" "no response"
fi

# ── Test 9: Mission Control ──────────────────────────────────────────────────
echo "9. Mission Control"
mc_nodes=$(curl -sf "$MC_API/nodes" 2>/dev/null)
if [ -n "$mc_nodes" ]; then
    mc_count=$(echo "$mc_nodes" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null)
    if [ "$mc_count" -ge 4 ]; then
        pass "MC has $mc_count nodes"
    else
        fail "MC nodes" "only $mc_count"
    fi
else
    fail "MC API" "unreachable"
fi

mc_services=$(curl -sf "$MC_API/services" 2>/dev/null)
if [ -n "$mc_services" ]; then
    pass "MC services API responds"
else
    fail "MC services" "unreachable"
fi

# MC heartbeat freshness — catch silent push failures
if [ -n "$mc_nodes" ]; then
    stale_nodes=$(echo "$mc_nodes" | python3 -c "
import json,sys
from datetime import datetime,timezone,timedelta
nodes = json.load(sys.stdin)
cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
stale = [n['name'] for n in nodes if datetime.fromisoformat(n['last_heartbeat'].replace('Z','+00:00')) < cutoff]
print(','.join(stale) if stale else '')
" 2>/dev/null)
    if [ -z "$stale_nodes" ]; then
        pass "MC heartbeats fresh (<10min)"
    else
        fail "MC heartbeats stale" "$stale_nodes"
    fi
fi

# ── Test 10: Budget API ──────────────────────────────────────────────────────
echo "10. Budget API"
budget=$(curl -sf "$API/budget" 2>/dev/null)
if echo "$budget" | grep -q "daily_usd"; then
    pass "Budget API returns spend data"
else
    fail "Budget API" "no spend data"
fi

node_metrics=$(curl -sf "$MC_API/nodes/heavy/metrics?days=1" 2>/dev/null)
if echo "$node_metrics" | python3 -c "import json,sys; d=json.load(sys.stdin); exit(0 if isinstance(d, list) else 1)" 2>/dev/null; then
    pass "Node metrics API responds"
else
    fail "Node metrics API" "not responding"
fi

budget_history=$(curl -sf "$MC_API/budget/history?days=1" 2>/dev/null)
if echo "$budget_history" | python3 -c "import json,sys; d=json.load(sys.stdin); exit(0 if isinstance(d, list) else 1)" 2>/dev/null; then
    pass "Budget history API responds"
else
    fail "Budget history API" "not responding"
fi

mc_budget=$(curl -sf "$MC_API/budget" 2>/dev/null)
if echo "$mc_budget" | python3 -c "import json,sys; d=json.load(sys.stdin); exit(0 if 'history' in d else 1)" 2>/dev/null; then
    pass "Budget includes history summary"
else
    fail "Budget history summary" "missing from MC /api/budget response"
fi

# ── Test 11: Disk space ─────────────────────────────────────────────────────
echo "11. Disk space"
for entry in "${NODES[@]}"; do
    IFS=: read -r name ssh_host <<< "$entry"
    disk_pct=$(cluster_ssh "$ssh_host" "df / | awk 'NR==2 {gsub(/%/,\"\"); print \$5}'" 2>/dev/null)
    if [ -n "$disk_pct" ] && [ "$disk_pct" -lt 90 ]; then
        pass "Disk $name (${disk_pct}%)"
    elif [ -n "$disk_pct" ]; then
        fail "Disk $name" "${disk_pct}% used (>90%)"
    else
        fail "Disk $name" "check failed"
    fi
done

# ── Test 12: Backup freshness + size ───────────────────────────────────────
echo "12. Backup health"
backup_info=$(cluster_ssh master "
    latest=\$(find /mnt/external/backups -name 'backup-*.tar.gz' -printf '%T@ %s %p\n' 2>/dev/null | sort -rn | head -1)
    if [ -n \"\$latest\" ]; then
        ts=\$(echo \$latest | cut -d' ' -f1 | cut -d. -f1)
        size_kb=\$(echo \$latest | awk '{print int(\$2/1024)}')
        age_h=\$(( (\$(date +%s) - ts) / 3600 ))
        echo \"\$age_h \$size_kb\"
    fi
" 2>/dev/null)
if [ -n "$backup_info" ]; then
    read -r age_h size_kb <<< "$backup_info"
    if [ "$age_h" -lt 25 ]; then
        pass "Backup age (${age_h}h)"
    else
        fail "Backup age" "${age_h}h old (>25h)"
    fi
    if [ "$size_kb" -gt 100 ]; then
        pass "Backup size (${size_kb}K)"
    else
        fail "Backup size" "${size_kb}K (expected >100K)"
    fi
else
    fail "Backup" "no backups found"
fi

# ── Test 13: MC API reachable from master ──────────────────────────────────
echo "13. Cross-node connectivity"
if cluster_ssh master "curl -sf --max-time 3 http://${HEAVY_IP:-192.168.0.5}:8000/health" > /dev/null 2>&1; then
    pass "MC API reachable from master"
else
    fail "MC API from master" "unreachable (check binding)"
fi
if cluster_ssh master "curl -sf --max-time 3 http://${HEAVY_IP:-192.168.0.5}:8520/health" > /dev/null 2>&1; then
    pass "Router API reachable from master"
else
    fail "Router API from master" "unreachable"
fi

# ── Test 13b: Port accessibility ──────────────────────────────────────────
echo "13b. Port accessibility"
HEAVY_LAN="${HEAVY_IP:-192.168.0.5}"
HEAVY_TS="${HEAVY_TAILSCALE_IP:-100.85.234.128}"
for node_entry in "slave1:light:${HEAVY_LAN}" "slave0:build:${HEAVY_TS}"; do
    IFS=: read -r ssh_host node_name target_ip <<< "$node_entry"
    port_results=$(cluster_ssh "$ssh_host" bash -c "
        curl -sf --max-time 5 http://${target_ip}:8520/health >/dev/null 2>&1 && echo 'OK:8520' || echo 'FAIL:8520'
        curl -sf --max-time 5 http://${target_ip}:18789/healthz >/dev/null 2>&1 && echo 'OK:18789' || echo 'FAIL:18789'
    " 2>/dev/null)
    for line in $port_results; do
        IFS=: read -r result port <<< "$line"
        [ "$result" = "OK" ] && pass "Port $port from $node_name" || fail "Port $port from $node_name" "unreachable (check UFW)"
    done
done

# ── Test 14: Channel providers ────────────────────────────────────────────
echo "14. Channel providers"
# Use log-based check — gateway CLI is too slow (~16s) for reliable testing
recent_logs=$(docker logs --since 2h "$GATEWAY" 2>&1)
if echo "$recent_logs" | grep -q "\[whatsapp\].*starting provider"; then
    pass "WhatsApp provider running"
else
    fail "WhatsApp provider" "no startup in last 2h"
fi
if echo "$recent_logs" | grep -q "\[telegram\].*starting provider"; then
    pass "Telegram provider running"
else
    fail "Telegram provider" "no startup in last 2h (run: channels add --channel telegram --token <TOKEN>)"
fi

tg_configured=$(docker exec "$GATEWAY" python3 -c "
import json
with open('/home/node/.openclaw/openclaw.json') as f:
    t = json.load(f).get('channels',{}).get('telegram',{})
    print('yes' if t.get('botToken') else 'no')
" 2>/dev/null)
[ "$tg_configured" = "yes" ] && pass "Telegram token persisted in config" || fail "Telegram token" "missing from gateway config"

# ── Test 15: Gateway restart recovery (--full only) ────────────────────────
FULL_MODE=false
[ "${1:-}" = "--full" ] && FULL_MODE=true

if $FULL_MODE; then
    echo "13. Gateway restart recovery"
    docker restart "$GATEWAY" > /dev/null 2>&1
    echo "  Waiting for gateway to recover..."
    recovered=false
    for i in $(seq 1 12); do
        sleep 10
        status=$(docker ps --filter "name=$GATEWAY" --format '{{.Status}}' 2>/dev/null)
        if echo "$status" | grep -q "healthy"; then
            recovered=true
            break
        fi
    done
    if $recovered; then
        pass "Gateway recovered in $((i * 10))s"
    else
        fail "Gateway recovery" "not healthy after 120s"
    fi
fi

# ── Test 16: Echo detection patch ────────────────────────────────────────────
echo "16. Echo detection patch"
patch_count=$(docker exec "$GATEWAY" grep -c "^[[:space:]]*group &&" \
  /app/extensions/whatsapp/src/inbound/monitor.ts 2>/dev/null)
fromMe_count=$(docker exec "$GATEWAY" grep -c "Boolean(msg.key?.fromMe)" \
  /app/extensions/whatsapp/src/inbound/monitor.ts 2>/dev/null)
if [ "${patch_count:-1}" -eq 0 ] && [ "${fromMe_count:-0}" -ge 1 ]; then
    pass "Echo detection patch applied (group && removed, fromMe check present)"
else
    fail "Echo detection patch" "group && count=$patch_count, fromMe count=$fromMe_count"
fi

# ── Test 17: Echo loop detection ─────────────────────────────────────────────
echo "17. Echo loop detection"
echo_chains=$(docker logs --since 10m "$GATEWAY" 2>&1 | python3 -c "
import sys, re
from datetime import datetime
events = []
for line in sys.stdin:
    line = line.strip()
    ts_match = re.match(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+\+\d{2}:\d{2})', line)
    if not ts_match: continue
    try: ts = datetime.fromisoformat(ts_match.group(1))
    except: continue
    if 'Auto-replied to' in line: events.append(('reply', ts))
    elif 'Inbound message' in line and 'direct' in line: events.append(('inbound', ts))
echoes = 0
for i in range(len(events) - 1):
    if events[i][0] == 'reply' and events[i+1][0] == 'inbound':
        delta = (events[i+1][1] - events[i][1]).total_seconds()
        if 0 < delta < 2: echoes += 1
replies = sum(1 for e in events if e[0] == 'reply')
inbounds = sum(1 for e in events if e[0] == 'inbound')
print(f'{echoes} {replies} {inbounds}')
" 2>/dev/null)
read -r echo_count reply_count inbound_count <<< "${echo_chains:-0 0 0}"
if [ -z "$echo_count" ] || [ "$echo_count" = "0" ]; then
    if [ "${reply_count:-0}" -gt 0 ]; then
        pass "No echo loops detected (${reply_count} replies, ${inbound_count} inbounds, 0 echoes)"
    else
        pass "No echo loops (no recent activity)"
    fi
else
    fail "Echo loop active" "$echo_count echoes in last 10m ($reply_count replies, $inbound_count inbounds)"
fi

# ── Test 18: Token consistency ───────────────────────────────────────────────
echo "18. Token consistency"
gw_token=$(docker exec "$GATEWAY" python3 -c "
import json
with open('/home/node/.openclaw/openclaw.json') as f:
    print(json.load(f)['gateway']['auth']['token'])
" 2>/dev/null)
env_token="${OPENCLAW_GATEWAY_TOKEN:-}"
if [ -n "$gw_token" ] && [ -n "$env_token" ] && [ "$gw_token" = "$env_token" ]; then
    pass "Gateway token matches env"
else
    fail "Token mismatch" "gateway='${gw_token:0:8}...' env='${env_token:0:8}...'"
fi

# ── Test 19: WhatsApp debounce ───────────────────────────────────────────────
echo "19. WhatsApp debounce"
debounce=$(docker exec "$GATEWAY" python3 -c "
import json
with open('/home/node/.openclaw/openclaw.json') as f:
    print(json.load(f).get('channels',{}).get('whatsapp',{}).get('debounceMs', 0))
" 2>/dev/null)
if [ "${debounce:-0}" -gt 0 ]; then
    pass "WhatsApp debounce set (${debounce}ms)"
else
    fail "WhatsApp debounce" "disabled — echo race condition risk"
fi

# ── Test 20: File permissions ────────────────────────────────────────────────
echo "20. File permissions"
expected_owner=$(id -un)
for f in "$HOME/openclaw/.env" "$HOME/.openclaw/openclaw.json" \
         "$SCRIPT_DIR/.env.cluster"; do
    [ ! -f "$f" ] && continue
    perms=$(stat -c '%a' "$f" 2>/dev/null)
    owner=$(stat -c '%U' "$f" 2>/dev/null)
    name=$(basename "$f")
    if [ "$perms" = "600" ]; then
        pass "Perms $name"
    else
        fail "Perms $name" "$perms (should be 600)"
    fi
    if [ "$owner" = "$expected_owner" ]; then
        pass "Owner $name"
    else
        fail "Owner $name" "owned by $owner (should be $expected_owner)"
    fi
done

# ── Test 21: Workspace write permissions ─────────────────────────────────────
echo "21. Workspace write permissions"
WS="/mnt/external/openclaw/workspace"

# Check no root-owned .md files in workspace root (root-owned = unwritable via NFS all_squash)
root_count=$(find "$WS" -maxdepth 1 -user root -name "*.md" 2>/dev/null | wc -l)
if [ "$root_count" -eq 0 ]; then
    pass "No root-owned workspace .md files"
else
    fail "Workspace ownership" "$root_count .md files owned by root (NFS squash blocks writes)"
fi

# Check gateway container can write to workspace
ws_test="/home/node/.openclaw/workspace/.perm-test-$$"
if docker exec "$GATEWAY" sh -c "touch $ws_test && rm -f $ws_test" 2>/dev/null; then
    pass "Gateway can write to workspace"
else
    fail "Gateway workspace write" "container cannot write (check NFS ownership)"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
test_summary

# Telegram notification on failure
if $TELEGRAM_MODE && [ "$FAIL" -gt 0 ]; then
    failures=$(printf '%s\n' "${TESTS[@]}" | grep "^FAIL" | head -5)
    send_telegram "🔴 *E2E Test Failed*
Passed: $PASS / Failed: $FAIL
$failures"
fi

echo ""
echo "=== E2E Test Complete $(date +%H:%M:%S) ==="
