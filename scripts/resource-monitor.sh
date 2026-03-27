#!/bin/bash
# Lightweight per-node resource monitor with Telegram alerts
# Runs via cron every 5 minutes on each node
# Only alerts on state CHANGE to avoid alert fatigue

set -uo pipefail

HOSTNAME=$(hostname)
STATE_FILE="/tmp/resource-monitor-state"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Thresholds
RAM_THRESHOLD=80    # alert when available < 20% (used > 80%)
SWAP_THRESHOLD=50   # alert when swap > 50% used
TEMP_THRESHOLD=75   # alert when temp > 75°C

# Load env for Telegram
# shellcheck source=scripts/.env.cluster
[ -f "$SCRIPT_DIR/.env.cluster" ] && source "$SCRIPT_DIR/.env.cluster"
# shellcheck source=scripts/lib/telegram.sh
source "$SCRIPT_DIR/lib/telegram.sh"

alerts=()

# RAM check
ram_pct=$(free | awk '/^Mem:/ {printf "%.0f", $3/$2 * 100}')
[ "$ram_pct" -gt "$RAM_THRESHOLD" ] && alerts+=("RAM ${ram_pct}%")

# Swap check
swap_total=$(free | awk '/^Swap:/ {print $2}')
if [ "$swap_total" -gt 0 ]; then
    swap_pct=$(free | awk '/^Swap:/ {printf "%.0f", $3/$2 * 100}')
    [ "$swap_pct" -gt "$SWAP_THRESHOLD" ] && alerts+=("Swap ${swap_pct}%")
fi

# Disk space check (alert on any filesystem >85%)
DISK_THRESHOLD=85
disk_pct=$(df / | awk 'NR==2 {gsub(/%/,""); print $5}')
[ "$disk_pct" -gt "$DISK_THRESHOLD" ] && alerts+=("Disk ${disk_pct}%")

# Temperature check (Pi 5 uses vcgencmd, x86 uses thermal zone)
temp=""
if command -v vcgencmd &>/dev/null; then
    temp=$(vcgencmd measure_temp 2>/dev/null | grep -oP '[\d.]+')
elif [ -f /sys/class/thermal/thermal_zone0/temp ]; then
    raw=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null)
    temp=$(echo "scale=1; $raw/1000" | bc 2>/dev/null)
fi
if [ -n "$temp" ]; then
    temp_int=${temp%.*}
    [ "$temp_int" -gt "$TEMP_THRESHOLD" ] && alerts+=("Temp ${temp}°C")
fi

# Build current state string
current_state=$(IFS="|"; echo "${alerts[*]:-OK}")

# Compare with previous state
prev_state=""
[ -f "$STATE_FILE" ] && prev_state=$(cat "$STATE_FILE")
echo "$current_state" > "$STATE_FILE"

# Alert only on state change
if [ "$current_state" != "$prev_state" ] && [ "$current_state" != "OK" ]; then
    ram_avail=$(free -m | awk '/^Mem:/ {print $7}')
    send_telegram "⚠️ *Resource Alert: $HOSTNAME*
${alerts[*]}
RAM available: ${ram_avail}MB
${temp:+Temp: ${temp}°C}"
fi

# Recovery alert
if [ "$current_state" = "OK" ] && [ -n "$prev_state" ] && [ "$prev_state" != "OK" ]; then
    send_telegram "✅ *Resource OK: $HOSTNAME* — recovered"
fi
