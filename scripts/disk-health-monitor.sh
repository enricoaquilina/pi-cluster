#!/bin/bash
# Disk health monitor: I/O rate, SMART, TRIM verification
# Runs every 5 min via cron on heavy. State-change Telegram alerts.
# Follows resource-monitor.sh pattern.

set -uo pipefail

HOSTNAME=$(hostname)
STATE_FILE="/tmp/disk-health-state"
IO_HISTORY_FILE="/tmp/disk-health-io-history"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Thresholds
IO_ALERT_MB=50       # alert if sustained writes > 50MB/s for 3 checks (15 min)
IO_WARN_MB=25        # hard alert at 25MB/s for 6 checks (30 min)
SMART_WEAR_PCT=50    # alert at wear > 50%
IO_SAMPLE_SECS=5     # sample duration for I/O measurement

# NVMe device (auto-detect)
NVME_DEV=$(lsblk -dpno NAME,TYPE | awk '$2=="disk" && /nvme/ {print $1; exit}')
NVME_DEV="${NVME_DEV:-/dev/nvme0n1}"

[ -f "$SCRIPT_DIR/.env.cluster" ] && source "$SCRIPT_DIR/.env.cluster"
# shellcheck source=scripts/lib/telegram.sh
source "$SCRIPT_DIR/lib/telegram.sh" 2>/dev/null || send_telegram() { :; }

alerts=()

# ── I/O Rate Check ──────────────────────────────────────────────────────────

# Get disk name for /proc/diskstats (e.g., nvme0n1)
DISK_NAME=$(basename "$NVME_DEV")

get_write_sectors() {
    awk -v dev="$DISK_NAME" '$3==dev {print $10}' /proc/diskstats 2>/dev/null || echo 0
}

sectors_before=$(get_write_sectors)
sleep "$IO_SAMPLE_SECS"
sectors_after=$(get_write_sectors)

# Sectors are 512 bytes each
sectors_written=$(( sectors_after - sectors_before ))
bytes_per_sec=$(( sectors_written * 512 / IO_SAMPLE_SECS ))
mb_per_sec=$(( bytes_per_sec / 1048576 ))

# Track I/O history for sustained alert detection
touch "$IO_HISTORY_FILE"
echo "$mb_per_sec" >> "$IO_HISTORY_FILE"
# Keep last 6 readings (30 min at 5-min intervals)
tail -6 "$IO_HISTORY_FILE" > "${IO_HISTORY_FILE}.tmp" && mv "${IO_HISTORY_FILE}.tmp" "$IO_HISTORY_FILE"

io_readings=$(wc -l < "$IO_HISTORY_FILE")

# Check for sustained high I/O (50MB/s for 3+ checks)
if [ "$io_readings" -ge 3 ]; then
    high_count=$(tail -3 "$IO_HISTORY_FILE" | awk -v t="$IO_ALERT_MB" '$1>t' | wc -l)
    if [ "$high_count" -ge 3 ]; then
        alerts+=("I/O CRITICAL: ${mb_per_sec}MB/s sustained >3 checks")
    fi
fi

# Check for slow burn (25MB/s for 6+ checks = 30 min)
if [ "$io_readings" -ge 6 ]; then
    warn_count=$(tail -6 "$IO_HISTORY_FILE" | awk -v t="$IO_WARN_MB" '$1>t' | wc -l)
    if [ "$warn_count" -ge 6 ]; then
        alerts+=("I/O WARNING: ${mb_per_sec}MB/s sustained >30min")
    fi
fi

# ── SMART Health Check (daily) ──────────────────────────────────────────────

SMART_STATE_FILE="/tmp/disk-health-smart-last"
now=$(date +%s)
last_smart=0
[ -f "$SMART_STATE_FILE" ] && last_smart=$(cat "$SMART_STATE_FILE" 2>/dev/null || echo 0)
smart_age=$(( now - last_smart ))

if [ "$smart_age" -gt 86400 ]; then  # Once per day
    echo "$now" > "$SMART_STATE_FILE"

    if command -v smartctl &>/dev/null; then
        smart_output=$(sudo smartctl -A "$NVME_DEV" 2>/dev/null)

        # NVMe: check Percentage Used
        pct_used=$(echo "$smart_output" | grep -i "Percentage Used" | awk '{print $NF}' | tr -d '%')
        if [ -n "$pct_used" ] && [ "$pct_used" -gt "$SMART_WEAR_PCT" ]; then
            alerts+=("SMART: ${pct_used}% wear (threshold: ${SMART_WEAR_PCT}%)")
        fi

        # Check media errors
        media_errors=$(echo "$smart_output" | grep -i "Media and Data Integrity" | awk '{print $NF}')
        if [ -n "$media_errors" ] && [ "$media_errors" -gt 0 ]; then
            alerts+=("SMART: ${media_errors} media errors")
        fi

        # Check critical warning
        critical=$(sudo smartctl -H "$NVME_DEV" 2>/dev/null | grep -i "SMART overall" | grep -ci "passed" || true)
        if [ "$critical" -eq 0 ]; then
            alerts+=("SMART: health check FAILED")
        fi
    fi
fi

# ── TRIM Verification (weekly) ──────────────────────────────────────────────

TRIM_STATE_FILE="/tmp/disk-health-trim-last"
last_trim=0
[ -f "$TRIM_STATE_FILE" ] && last_trim=$(cat "$TRIM_STATE_FILE" 2>/dev/null || echo 0)
trim_age=$(( now - last_trim ))

if [ "$trim_age" -gt 604800 ]; then  # Once per week
    echo "$now" > "$TRIM_STATE_FILE"

    # Check fstrim.timer is active
    if ! systemctl is-active fstrim.timer &>/dev/null; then
        alerts+=("TRIM: fstrim.timer not active")
    fi

    # Check discard mount option or fstrim last run
    if ! systemctl list-timers fstrim.timer 2>/dev/null | grep -q "fstrim"; then
        alerts+=("TRIM: fstrim timer not scheduled")
    fi
fi

# ── State Change Detection ──────────────────────────────────────────────────

current_state=$(IFS="|"; echo "${alerts[*]:-OK}")

prev_state=""
[ -f "$STATE_FILE" ] && prev_state=$(cat "$STATE_FILE")
echo "$current_state" > "$STATE_FILE"

if [ "$current_state" != "$prev_state" ] && [ "$current_state" != "OK" ]; then
    send_telegram "⚠️ *Disk Health: $HOSTNAME*
${alerts[*]}
Current write rate: ${mb_per_sec}MB/s
Device: $NVME_DEV"
fi

if [ "$current_state" = "OK" ] && [ -n "$prev_state" ] && [ "$prev_state" != "OK" ]; then
    send_telegram "✅ *Disk Health OK: $HOSTNAME* — recovered"
fi
