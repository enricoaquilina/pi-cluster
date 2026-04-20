#!/usr/bin/env bats
# Tests for NFS backup sync (heavy → master).
#
# Validates that the backup timer exists, runs on schedule,
# excludes secrets, and targets master as the backup destination.

@test "nfs-backup systemd service file exists" {
    [ -f /etc/systemd/system/nfs-backup.service ]
}

@test "nfs-backup systemd timer file exists" {
    [ -f /etc/systemd/system/nfs-backup.timer ]
}

@test "nfs-backup timer fires every 6 hours" {
    grep -q "OnCalendar=.*00/6" /etc/systemd/system/nfs-backup.timer
}

@test "backup excludes .env files" {
    grep -q "\-\-exclude=.*\.env" /etc/systemd/system/nfs-backup.service
}

@test "backup target is master:/mnt/external" {
    grep -q "master:/mnt/external" /etc/systemd/system/nfs-backup.service
}

@test "backup source is /mnt/data" {
    grep -q "/mnt/data" /etc/systemd/system/nfs-backup.service
}

@test "nfs-backup timer is enabled" {
    systemctl is-enabled nfs-backup.timer
}
