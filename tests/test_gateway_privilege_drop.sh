#!/bin/bash
# Tests: Gateway privilege drop — verifies container runs gateway as uid 1000
# Requires: gateway container running on heavy
#
# 8 test cases verifying privilege drop, file ownership, init steps.

set -uo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$TEST_DIR/.." && pwd)"

source "$REPO_DIR/scripts/lib/test-harness.sh"

echo "=== Gateway Privilege Drop Tests ==="

CONTAINER="openclaw-openclaw-gateway-1"
WORKSPACE="/mnt/data/openclaw/workspace"

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    echo "SKIP: gateway container not running"
    exit 0
fi

# ── A: Docker Compose Configuration ──────────────────────────────────────────

compose_cmd=$(docker inspect "$CONTAINER" --format '{{.Config.Cmd}}' 2>/dev/null)

echo "$compose_cmd" | grep -q "setpriv" && \
    pass "A1: docker-compose command includes setpriv" || \
    fail "A1: docker-compose command includes setpriv" "got: $compose_cmd"

echo "$compose_cmd" | grep -q "reuid=1000" && \
    pass "A2: setpriv drops to uid 1000" || \
    fail "A2: setpriv drops to uid 1000" "got: $compose_cmd"

# ── B: Running Process UID ───────────────────────────────────────────────────

gateway_user=$(docker exec "$CONTAINER" ps aux 2>/dev/null | grep "[o]penclaw-gateway" | awk '{print $1}' | head -1)

[ "$gateway_user" = "node" ] && \
    pass "B1: gateway process runs as node user" || \
    fail "B1: gateway process runs as node user" "got: ${gateway_user:-not found}"

gateway_uid=$(docker exec "$CONTAINER" id -u 2>/dev/null)
# id runs as PID 1's user (root/init), so check the actual node process
gateway_uid=$(docker exec "$CONTAINER" sh -c 'cat /proc/$(pgrep -f "openclaw-gateway" | head -1)/status 2>/dev/null | grep "^Uid:" | awk "{print \$2}"')

[ "$gateway_uid" = "1000" ] && \
    pass "B2: gateway process UID is 1000" || \
    fail "B2: gateway process UID is 1000" "got: ${gateway_uid:-unknown}"

# ── C: Init Steps Still Work ─────────────────────────────────────────────────

docker exec "$CONTAINER" test -L /home/enrico 2>/dev/null && \
    pass "C1: /home/enrico symlink exists in container" || \
    fail "C1: /home/enrico symlink exists in container" "symlink missing"

symlink_target=$(docker exec "$CONTAINER" readlink /home/enrico 2>/dev/null)
[ "$symlink_target" = "/home/node" ] && \
    pass "C2: symlink points to /home/node" || \
    fail "C2: symlink points to /home/node" "got: ${symlink_target:-missing}"

# ── D: Workspace File Ownership ──────────────────────────────────────────────

root_count=$(find "$WORKSPACE" -maxdepth 3 -user root \
    ! -path "*/node_modules/*" ! -path "*/.git/*" 2>/dev/null | wc -l)

[ "$root_count" -eq 0 ] && \
    pass "D1: no root-owned files in workspace" || \
    fail "D1: no root-owned files in workspace" "${root_count} root files found"

# ── E: Gateway Health ────────────────────────────────────────────────────────

health=$(curl -sf --max-time 5 http://localhost:18789/healthz 2>/dev/null)
echo "$health" | grep -q '"ok":true' && \
    pass "E1: gateway healthcheck passes after privilege drop" || \
    fail "E1: gateway healthcheck passes" "got: ${health:-unreachable}"

test_summary
