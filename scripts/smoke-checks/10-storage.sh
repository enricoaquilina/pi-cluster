#!/bin/bash
# Checks: NFS workspace accessibility + NFS backup timer health

_NFS_OWNERSHIP_DATA=""

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

    if [ -z "$_NFS_OWNERSHIP_DATA" ]; then
        _NFS_OWNERSHIP_DATA=$(timeout 10 find "$ws" -maxdepth 3 \
            -user root \
            ! -path "*/node_modules/*" \
            ! -path "*/.git/*" \
            -printf '%T@ %p\n' 2>/dev/null | sort -rn)
        [ -z "$_NFS_OWNERSHIP_DATA" ] && _NFS_OWNERSHIP_DATA="clean"
    fi

    if [ "$_NFS_OWNERSHIP_DATA" = "clean" ]; then
        check_service "nfs-workspace" "up"
        return
    fi

    local root_count newest_file
    root_count=$(echo "$_NFS_OWNERSHIP_DATA" | wc -l)
    newest_file=$(echo "$_NFS_OWNERSHIP_DATA" | head -1 | cut -d' ' -f2-)
    newest_file="${newest_file#"$ws"/}"

    if [ "${root_count:-0}" -gt 10 ]; then
        check_service "nfs-workspace" "down" "${root_count}+ root-owned files (newest: ${newest_file})"
    else
        check_service "nfs-workspace" "degraded" "${root_count} root-owned files (newest: ${newest_file})"
    fi
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
