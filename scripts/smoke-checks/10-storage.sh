#!/bin/bash
# Checks: NFS workspace accessibility + NFS backup timer health

check_nfs_workspace() {
    local ws
    if [ "$(hostname)" = "heavy" ]; then
        ws="/mnt/data/openclaw/workspace"
    else
        ws="/mnt/external/openclaw/workspace"
    fi

    if ! timeout 5 stat "$ws" >/dev/null 2>&1; then
        check_service "nfs-workspace" "down" "workspace unresponsive or missing ($ws)"
        return
    fi

    local root_count
    root_count=$(find "$ws" -maxdepth 2 -user root 2>/dev/null | wc -l)
    if [ "${root_count:-0}" -gt 0 ]; then
        check_service "nfs-workspace" "degraded" "${root_count} root-owned files in workspace"
        return
    fi

    check_service "nfs-workspace" "up"
}

check_nfs_backup() {
    if ! systemctl is-active --quiet nfs-backup.timer 2>/dev/null; then
        check_service "nfs-backup" "down" "Timer not active"
        return
    fi
    local last_run
    last_run=$(systemctl show nfs-backup.service --property=ExecMainExitTimestampMonotonic --value 2>/dev/null)
    if [[ -z "$last_run" || "$last_run" == "0" ]]; then
        check_service "nfs-backup" "up"
        return
    fi
    local last_exit_code
    last_exit_code=$(systemctl show nfs-backup.service --property=ExecMainStatus --value 2>/dev/null)
    if [[ "$last_exit_code" != "0" ]]; then
        check_service "nfs-backup" "degraded" "Last run exited with code $last_exit_code"
        return
    fi
    check_service "nfs-backup" "up"
}
