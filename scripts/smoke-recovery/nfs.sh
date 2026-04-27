#!/bin/bash
# Auto-recovery: NFS server exports + client stale mounts
# Sourced in cron mode by system-smoke-test.sh

_nfs_server_fails=$(cat "$FAIL_COUNT_DIR/nfs-server.count" 2>/dev/null || echo "0")
_nfs_mount_fails=$(cat "$FAIL_COUNT_DIR/nfs-mount.count" 2>/dev/null || echo "0")

# Server-side: NFS exports disappeared (3+ consecutive failures = 15+ min)
if [[ "$_nfs_server_fails" -ge 3 ]]; then
    if ! check_circuit_breaker; then
        echo "[${NOW}] Circuit breaker tripped — skipping NFS server recovery" >> "$LOG_FILE"
    else
        send_alert "AUTO-RECOVERY: NFS server not exporting for 15+ min — re-exporting"
        exportfs -ra 2>/dev/null || true
        sleep 3
        if showmount -e localhost 2>/dev/null | grep -q /mnt/data; then
            send_alert "AUTO-RECOVERY SUCCESS: NFS exports restored via exportfs -ra"
            echo "0" > "$FAIL_COUNT_DIR/nfs-server.count"
            echo "up" > "$STATE_DIR/nfs-server.status"
        else
            send_alert "AUTO-RECOVERY: exportfs -ra failed — restarting nfs-kernel-server"
            sudo systemctl restart nfs-kernel-server 2>/dev/null || true
            sleep 5
            if showmount -e localhost 2>/dev/null | grep -q /mnt/data; then
                send_alert "AUTO-RECOVERY SUCCESS: NFS server restored after full restart"
                echo "0" > "$FAIL_COUNT_DIR/nfs-server.count"
                echo "up" > "$STATE_DIR/nfs-server.status"
            else
                send_alert "AUTO-RECOVERY FAILED: NFS server still not exporting"
            fi
        fi
    fi
fi

# Client-side: stale/missing mounts (3+ consecutive failures = 15+ min)
if [[ "$_nfs_mount_fails" -ge 3 ]]; then
    if ! check_circuit_breaker; then
        echo "[${NOW}] Circuit breaker tripped — skipping NFS client recovery" >> "$LOG_FILE"
    else
        _nfs_recovered=true
        for _nfs_node in master slave0 slave1; do
            _nfs_ok=$(timed_ssh 5 "$_nfs_node" "mountpoint -q /mnt/external && timeout 3 stat -t /mnt/external >/dev/null 2>&1 && echo ok" 2>/dev/null)
            if [[ "$_nfs_ok" != "ok" ]]; then
                send_alert "AUTO-RECOVERY: Remounting NFS on ${_nfs_node}"
                timed_ssh 15 "$_nfs_node" "sudo umount -f -l /mnt/external 2>/dev/null; sudo mount /mnt/external" 2>/dev/null || true
                sleep 3
                _nfs_verify=$(timed_ssh 5 "$_nfs_node" "timeout 3 stat -t /mnt/external >/dev/null 2>&1 && echo ok" 2>/dev/null)
                if [[ "$_nfs_verify" != "ok" ]]; then
                    send_alert "AUTO-RECOVERY FAILED: NFS remount on ${_nfs_node}"
                    _nfs_recovered=false
                fi
            fi
        done
        if [[ "$_nfs_recovered" == true ]]; then
            send_alert "AUTO-RECOVERY SUCCESS: NFS mounts restored"
            echo "0" > "$FAIL_COUNT_DIR/nfs-mount.count"
            echo "up" > "$STATE_DIR/nfs-mount.status"
        fi
    fi
fi
