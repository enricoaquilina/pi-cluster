#!/bin/bash
# SSH watchdog: restart sshd if it dies, alert via Telegram
# Runs every 2 min via systemd timer on heavy.
# Checks ssh.socket is active AND port 22 is listening.
# State-change alerts only (same dedup pattern as resource-monitor).

set -uo pipefail

STATE_FILE="/tmp/ssh-watchdog-state"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[ -f "$SCRIPT_DIR/.env.cluster" ] && source "$SCRIPT_DIR/.env.cluster"
# shellcheck source=scripts/lib/telegram.sh
source "$SCRIPT_DIR/lib/telegram.sh" 2>/dev/null || send_telegram() { :; }

current_state="OK"

# Check ssh.socket is active
if ! systemctl is-active ssh.socket &>/dev/null && ! systemctl is-active sshd &>/dev/null; then
    current_state="DOWN"
    logger -t ssh-watchdog "ssh.socket/sshd not active, restarting..."
    systemctl restart ssh.socket 2>/dev/null || systemctl restart sshd 2>/dev/null
    sleep 2
    if systemctl is-active ssh.socket &>/dev/null || systemctl is-active sshd &>/dev/null; then
        current_state="RECOVERED"
    fi
fi

# Verify port 22 is actually listening
if [ "$current_state" = "OK" ] && ! ss -tlnp | grep -q ':22 '; then
    current_state="PORT_DOWN"
    logger -t ssh-watchdog "Port 22 not listening, restarting sshd..."
    systemctl restart ssh.socket 2>/dev/null || systemctl restart sshd 2>/dev/null
    sleep 2
    if ss -tlnp | grep -q ':22 '; then
        current_state="RECOVERED"
    fi
fi

# Compare with previous state
prev_state=""
[ -f "$STATE_FILE" ] && prev_state=$(cat "$STATE_FILE")
echo "$current_state" > "$STATE_FILE"

# Alert on state change
if [ "$current_state" != "$prev_state" ]; then
    case "$current_state" in
        DOWN)
            send_telegram "🚨 *SSH Watchdog: heavy* — sshd down, restart FAILED"
            ;;
        PORT_DOWN)
            send_telegram "🚨 *SSH Watchdog: heavy* — port 22 not listening, restart FAILED"
            ;;
        RECOVERED)
            send_telegram "🔧 *SSH Watchdog: heavy* — sshd was down, auto-recovered"
            ;;
        OK)
            if [ -n "$prev_state" ] && [ "$prev_state" != "OK" ]; then
                send_telegram "✅ *SSH Watchdog: heavy* — sshd back to normal"
            fi
            ;;
    esac
fi
