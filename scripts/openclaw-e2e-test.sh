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

set -uo pipefail

PASS=0
FAIL=0
TESTS=()
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API="http://127.0.0.1:8520"
MC_API="http://127.0.0.1:8000/api"
GATEWAY="openclaw-openclaw-gateway-1"

# Nodes: name:ssh_host
NODES=("control:master" "build:slave0" "light:slave1" "heavy:heavy")

TELEGRAM_MODE=false
[ "${1:-}" = "--telegram" ] && TELEGRAM_MODE=true

pass() {
    PASS=$((PASS + 1))
    TESTS+=("PASS  $1")
    echo "  PASS  $1"
}

fail() {
    FAIL=$((FAIL + 1))
    TESTS+=("FAIL  $1: $2")
    echo "  FAIL  $1: $2"
}

echo "=== OpenClaw E2E Test Suite ==="
date
echo ""

# ── Test 1: SSH connectivity ─────────────────────────────────────────────────
echo "1. SSH connectivity"
for entry in "${NODES[@]}"; do
    IFS=: read -r name ssh_host <<< "$entry"
    if ssh -o ConnectTimeout=5 -o BatchMode=yes "$ssh_host" "echo ok" > /dev/null 2>&1; then
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

# ── Test 3: Gateway connectivity ─────────────────────────────────────────────
echo "3. Gateway connectivity"
gw_status=$(docker ps --filter "name=$GATEWAY" --format '{{.Status}}' 2>/dev/null)
if echo "$gw_status" | grep -q "healthy"; then
    pass "Gateway container healthy"
else
    fail "Gateway container" "$gw_status"
fi

connected=$(docker exec "$GATEWAY" sh -c 'OPENCLAW_GATEWAY_TOKEN=REDACTED_GATEWAY_TOKEN timeout 10 node dist/index.js nodes status 2>&1' | grep "paired.*connected" | grep -vc "disconnected")
if [ "$connected" -eq 4 ]; then
    pass "All 4 nodes connected to gateway"
else
    fail "Gateway nodes" "only $connected/4 connected"
fi

# ── Test 4: Dispatch to each node ────────────────────────────────────────────
echo "4. Dispatch execution"
for entry in "${NODES[@]}"; do
    IFS=: read -r name _ <<< "$entry"
    result=$(docker exec "$GATEWAY" sh -c "OPENCLAW_GATEWAY_TOKEN=REDACTED_GATEWAY_TOKEN timeout 15 node dist/index.js nodes run --node $name --raw 'echo E2E_OK_$name'" 2>&1)
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
    result=$(docker exec "$GATEWAY" sh -c "OPENCLAW_GATEWAY_TOKEN=REDACTED_GATEWAY_TOKEN timeout 15 node dist/index.js nodes run --node $name --raw 'bash -c \"python3 -c \\\"print(\\\\\\\"PY_OK\\\\\\\")\\\"\"'" 2>&1)
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
    result=$(ssh -o ConnectTimeout=5 -o BatchMode=yes "$ssh_host" \
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

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "=== Results ==="
echo "Passed: $PASS"
echo "Failed: $FAIL"
echo "Total:  $((PASS + FAIL))"

if [ "$FAIL" -gt 0 ]; then
    echo ""
    echo "Failures:"
    for t in "${TESTS[@]}"; do
        echo "$t" | grep "^FAIL" || true
    done
fi

# Telegram notification on failure
if $TELEGRAM_MODE && [ "$FAIL" -gt 0 ]; then
    ENV_FILE="$SCRIPT_DIR/.env.cluster"
    if [ -f "$ENV_FILE" ]; then
        # shellcheck source=/dev/null
        source "$ENV_FILE"
    fi
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
        failures=$(printf '%s\n' "${TESTS[@]}" | grep "^FAIL" | head -5)
        curl -sf -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${TELEGRAM_CHAT_ID:-1630148884}" \
            -d "text=🔴 *E2E Test Failed*
Passed: $PASS / Failed: $FAIL
$failures" \
            -d "parse_mode=Markdown" > /dev/null 2>&1
    fi
fi

echo ""
echo "=== E2E Test Complete $(date +%H:%M:%S) ==="
[ "$FAIL" -eq 0 ]
