#!/bin/bash
set -uo pipefail
# Sends Wake-on-LAN magic packet to heavy and verifies it comes up.
# Requires: wakeonlan package on master, WoL enabled on heavy (ethtool + BIOS).
#
# Usage: bash scripts/wake-heavy.sh
#        bash scripts/wake-heavy.sh --no-wait   # send packet only, don't wait

HEAVY_MAC="84:47:09:74:59:07"
HEAVY_IP="${HEAVY_IP:-192.168.0.5}"
WAIT_TIMEOUT=120  # seconds to wait for boot
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/.env.cluster" 2>/dev/null || true
# shellcheck source=scripts/lib/telegram.sh
source "$SCRIPT_DIR/lib/telegram.sh" 2>/dev/null || send_telegram() { :; }

# Check if already up
if ping -c1 -W3 "$HEAVY_IP" >/dev/null 2>&1; then
    echo "Heavy ($HEAVY_IP) is already reachable."
    exit 0
fi

echo "Sending WoL magic packet to heavy ($HEAVY_MAC)..."
wakeonlan "$HEAVY_MAC" || { echo "FAILED: wakeonlan command failed"; exit 1; }

if [ "${1:-}" = "--no-wait" ]; then
    echo "Packet sent. Use 'ping $HEAVY_IP' to check when it's up."
    exit 0
fi

echo "Waiting up to ${WAIT_TIMEOUT}s for heavy to boot..."
ELAPSED=0
while [ "$ELAPSED" -lt "$WAIT_TIMEOUT" ]; do
    sleep 10
    ELAPSED=$((ELAPSED + 10))
    if ping -c1 -W3 "$HEAVY_IP" >/dev/null 2>&1; then
        echo "Heavy is UP after ${ELAPSED}s."
        send_telegram "✅ Heavy woke via WoL after ${ELAPSED}s — $(date '+%H:%M')"
        exit 0
    fi
    echo "  ${ELAPSED}s — not yet reachable..."
done

echo "TIMEOUT: Heavy did not respond after ${WAIT_TIMEOUT}s."
echo "Possible causes:"
echo "  - BIOS WoL not enabled (requires physical access to BIOS)"
echo "  - BIOS 'Restore on AC Power Loss' not set to 'Power On'"
echo "  - ErP/EuP mode enabled in BIOS (cuts NIC power when off)"
echo "  - Machine is powered off at the wall switch"
send_telegram "⚠️ WoL failed — heavy did not respond after ${WAIT_TIMEOUT}s"
exit 1
