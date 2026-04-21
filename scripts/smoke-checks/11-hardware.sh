#!/bin/bash
# Checks: NVMe SMART health + real-time write rate sampling

check_nvme_health() {
    local smart_out
    smart_out=$(sudo smartctl -A /dev/nvme0 2>/dev/null)
    if [[ -z "$smart_out" ]]; then
        check_service "nvme-health" "down" "Cannot read SMART data"
        return
    fi

    local pct_used temp_c avail_spare media_errors
    pct_used=$(echo "$smart_out" | awk '/Percentage Used:/ {print $3}' | tr -d '%')
    temp_c=$(echo "$smart_out" | awk '/^Temperature:/ {print $2}')
    avail_spare=$(echo "$smart_out" | awk '/Available Spare:/ {gsub(/%/,""); print $3}')
    media_errors=$(echo "$smart_out" | awk '/Media and Data Integrity Errors:/ {print $NF}')

    local issues=()

    if [[ -n "$media_errors" && "$media_errors" -gt 0 ]]; then
        issues+=("${media_errors} media errors")
    fi

    if [[ -n "$pct_used" && "$pct_used" -ge 80 ]]; then
        issues+=("${pct_used}% used")
    fi

    if [[ -n "$temp_c" && "$temp_c" -ge 70 ]]; then
        issues+=("${temp_c}°C")
    fi

    if [[ -n "$avail_spare" && "$avail_spare" -le 10 ]]; then
        issues+=("spare ${avail_spare}%")
    fi

    # Check daily write rate from the wear log (written by disk-health-monitor)
    local wear_log="/var/log/nvme-wear.log"
    if [[ -f "$wear_log" ]]; then
        local last_delta_gb
        last_delta_gb=$(tail -1 "$wear_log" 2>/dev/null | awk -F',' '{print $4}' | tr -d ' ')
        if [[ -n "$last_delta_gb" ]] && awk "BEGIN{exit !($last_delta_gb > 20)}" 2>/dev/null; then
            issues+=("${last_delta_gb}GB written today")
        fi
    fi

    if [[ ${#issues[@]} -gt 0 ]]; then
        local severity="degraded"
        # Escalate to down for media errors or critical wear
        [[ -n "$media_errors" && "$media_errors" -gt 0 ]] && severity="down"
        [[ -n "$pct_used" && "$pct_used" -ge 90 ]] && severity="down"
        [[ -n "$temp_c" && "$temp_c" -ge 85 ]] && severity="down"
        check_service "nvme-health" "$severity" "$(IFS=', '; echo "${issues[*]}")"
    else
        check_service "nvme-health" "up"
    fi
}

check_nvme_write_rate() {
    local disk_name="nvme0n1"
    local sectors_before sectors_after sectors_written mb_per_sec

    sectors_before=$(awk -v dev="$disk_name" '$3==dev {print $10}' /proc/diskstats 2>/dev/null || echo 0)
    sleep 3
    sectors_after=$(awk -v dev="$disk_name" '$3==dev {print $10}' /proc/diskstats 2>/dev/null || echo 0)

    sectors_written=$(( sectors_after - sectors_before ))
    mb_per_sec=$(( sectors_written * 512 / 3 / 1048576 ))

    if [[ "$mb_per_sec" -gt 50 ]]; then
        check_service "nvme-write-rate" "down" "${mb_per_sec}MB/s sustained writes"
    elif [[ "$mb_per_sec" -gt 20 ]]; then
        check_service "nvme-write-rate" "degraded" "${mb_per_sec}MB/s writes"
    else
        check_service "nvme-write-rate" "up"
    fi
}
