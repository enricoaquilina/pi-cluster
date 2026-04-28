#!/bin/bash
# OpenClaw Version Check & Safe Upgrade
# Checks if a newer version is available, tests it, and upgrades if the
# SYSTEM_RUN_DENIED bug is fixed. Rolls back if the bug persists.
#
# Usage:
#   bash scripts/openclaw-version-check.sh          # Check only
#   bash scripts/openclaw-version-check.sh --upgrade # Check and upgrade if safe
#
# Designed to run as a daily cron job on master. Upgrades all 4 nodes.

set -uo pipefail

UPGRADE_MODE=false
[ "${1:-}" = "--upgrade" ] && UPGRADE_MODE=true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PINNED_VERSION=$(grep '^openclaw_version:' "$SCRIPT_DIR/../vars/openclaw-nodes.yml" 2>/dev/null | sed 's/.*"\(.*\)".*/\1/')
if [ -z "$PINNED_VERSION" ]; then
    echo "ERROR: Cannot read openclaw_version from vars/openclaw-nodes.yml"
    exit 1
fi
NODES=("master" "slave0" "slave1" "heavy")
GATEWAY_CONTAINER="openclaw-openclaw-gateway-1"

# shellcheck source=scripts/.env.cluster
[ -f "$SCRIPT_DIR/.env.cluster" ] && source "$SCRIPT_DIR/.env.cluster"
# shellcheck source=scripts/lib/telegram.sh
source "$SCRIPT_DIR/lib/telegram.sh" 2>/dev/null || send_telegram() { :; }

echo "=== OpenClaw Version Check ==="
echo "Pinned: $PINNED_VERSION"

# Check current versions
for host in "${NODES[@]}"; do
    if [ "$host" = "$(hostname -s)" ]; then
        ver=$(openclaw --version 2>/dev/null | grep -oP '[0-9]+\.[0-9]+\.[0-9]+' || echo "unknown")
    else
        ver=$(ssh -o ConnectTimeout=5 -o BatchMode=yes "$host" "openclaw --version 2>/dev/null | grep -oP '[0-9]+\.[0-9]+\.[0-9]+'" 2>/dev/null || echo "unreachable")
    fi
    echo "$host: $ver"
done

# Check latest available
latest=$(npm view openclaw version 2>/dev/null || echo "unknown")
echo "Latest on npm: $latest"

if [ "$latest" = "unknown" ] || [ "$latest" = "$PINNED_VERSION" ]; then
    echo "No new version available."
    exit 0
fi

echo ""
echo "New version available: $latest (current pin: $PINNED_VERSION)"

if ! $UPGRADE_MODE; then
    echo "Run with --upgrade to test and apply."
    exit 0
fi

# Test upgrade on one node first (light — least critical)
test_node="slave1"
echo ""
echo "Testing $latest on $test_node..."

# Install new version
ssh "$test_node" "sudo npm install -g openclaw@$latest 2>&1 | tail -2" 2>/dev/null
installed=$(ssh "$test_node" "openclaw --version 2>/dev/null | grep -oP '[0-9]+\.[0-9]+\.[0-9]+'" 2>/dev/null)
echo "Installed: $installed"

# Re-pair and test interpreter command
ssh "$test_node" "sudo systemctl restart openclaw-node" 2>/dev/null
sleep 15
bash "$SCRIPT_DIR/openclaw-pair-nodes.sh" > /dev/null 2>&1
sleep 5

# The critical test: does python3 work?
test_result=$(docker exec "$GATEWAY_CONTAINER" openclaw nodes run --node light --raw "python3 -c 'print(\"VERSION_TEST_OK\")'" 2>&1)

if echo "$test_result" | grep -q "VERSION_TEST_OK"; then
    echo "PASS: python3 works on $latest"
    echo ""
    echo "Upgrading remaining nodes..."

    # Upgrade remaining nodes (master runs locally, others via SSH)
    for host in master slave0 heavy; do
        if [ "$host" = "$(hostname -s)" ]; then
            sudo bash -c "rm -rf /usr/lib/node_modules/openclaw && npm install -g openclaw@$latest" 2>/dev/null | tail -1
            sudo systemctl restart openclaw-node 2>/dev/null || true
        else
            ssh "$host" "sudo bash -c 'rm -rf /usr/lib/node_modules/openclaw && npm install -g openclaw@$latest'" 2>/dev/null | tail -1
            ssh "$host" "sudo systemctl restart openclaw-node" 2>/dev/null || true
        fi
        echo "$host: upgraded"
    done

    # Re-pair all
    bash "$SCRIPT_DIR/openclaw-pair-nodes.sh" > /dev/null 2>&1

    # Update the pin in vars
    sed -i "s/openclaw_version: \"$PINNED_VERSION\"/openclaw_version: \"$latest\"/" "$SCRIPT_DIR/../vars/openclaw-nodes.yml"

    echo ""
    echo "All nodes upgraded to $latest"
    send_telegram "🆙 *OpenClaw Upgraded*
All nodes updated from $PINNED_VERSION to $latest.
Interpreter test passed."
else
    echo "FAIL: SYSTEM_RUN_DENIED bug still present in $latest"
    echo "Rolling back $test_node to $PINNED_VERSION..."

    if [ "$test_node" = "$(hostname -s)" ]; then
        sudo bash -c "rm -rf /usr/lib/node_modules/openclaw && npm install -g openclaw@$PINNED_VERSION" 2>/dev/null | tail -1
        sudo systemctl restart openclaw-node 2>/dev/null
    else
        ssh "$test_node" "sudo bash -c 'rm -rf /usr/lib/node_modules/openclaw && npm install -g openclaw@$PINNED_VERSION'" 2>/dev/null | tail -1
        ssh "$test_node" "sudo systemctl restart openclaw-node" 2>/dev/null
    fi
    bash "$SCRIPT_DIR/openclaw-pair-nodes.sh" > /dev/null 2>&1

    echo "Rolled back. Staying on $PINNED_VERSION."
    send_telegram "⚠️ *OpenClaw Upgrade Failed*
Tested $latest — SYSTEM_RUN_DENIED bug still present.
Rolled back to $PINNED_VERSION."
fi
