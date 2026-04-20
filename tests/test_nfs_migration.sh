#!/usr/bin/env bats
# Tests for NFS architecture: heavy as primary server.
#
# Validates that heavy exports /mnt/data via NFS, secrets are extracted,
# workspace ownership is correct, and Docker containers use the new paths.

setup() {
    REPO_DIR="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
}

@test "heavy exports /mnt/data via NFS" {
    showmount -e localhost | grep -q "/mnt/data"
}

@test "NFS export restricts to LAN only (no Tailscale 100.64.0.0)" {
    local exports
    exports=$(cat /etc/exports.d/openclaw.exports 2>/dev/null)
    [[ "$exports" == *"/mnt/data"* ]]
    [[ "$exports" != *"100.64.0.0"* ]]
}

@test "NFS ports open in UFW for cluster subnet" {
    sudo ufw status | grep "2049" | grep -q "192.168.0.0/24"
}

@test "no .env files in NFS export path" {
    [ -d /mnt/data ]
    local count
    count=$(find /mnt/data -name ".env" 2>/dev/null | wc -l)
    [ "$count" -eq 0 ]
}

@test "secrets directory exists with mode 0700" {
    [ -d "$HOME/secrets" ]
    local perms
    perms=$(stat -c %a "$HOME/secrets")
    [ "$perms" = "700" ]
}

@test "secret files have mode 0600" {
    local found=0
    for f in "$HOME/secrets"/*.env; do
        [ -f "$f" ] || continue
        found=1
        local perms
        perms=$(stat -c %a "$f")
        [ "$perms" = "600" ]
    done
    [ "$found" -eq 1 ]
}

@test "workspace files owned by enrico (not root)" {
    [ -d /mnt/data/openclaw/workspace ]
    local root_count
    root_count=$(find /mnt/data/openclaw/workspace -maxdepth 2 -user root 2>/dev/null | wc -l)
    [ "$root_count" -eq 0 ]
}

@test "openclaw compose file exists and is not a broken symlink" {
    [ -f "$HOME/openclaw/docker-compose.yml" ]
    [ -s "$HOME/openclaw/docker-compose.yml" ]
}

@test "docker containers use /mnt/data paths" {
    docker inspect openclaw-openclaw-gateway-1 --format '{{json .Mounts}}' | grep -q "/mnt/data"
}

@test "smoke test reports nfs-workspace as up" {
    local result
    result=$(bash "$REPO_DIR/scripts/system-smoke-test.sh" --json 2>/dev/null)
    echo "$result" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for s in d['services']:
    if s['service'] == 'nfs-workspace':
        assert s['status'] == 'up', f\"got {s['status']}: {s.get('error', '')}\"
        sys.exit(0)
sys.exit(1)
"
}

@test "nfs-backup timer is active" {
    systemctl is-active nfs-backup.timer
}

@test "master NFS server is disabled" {
    run ssh -o ConnectTimeout=5 -o BatchMode=yes master "systemctl is-active nfs-kernel-server" 2>/dev/null
    [ "$status" -ne 0 ]
}

@test "vars/openclaw-nodes.yml points nfs_server to heavy" {
    grep -q "nfs_server: 192.168.0.5" "$REPO_DIR/vars/openclaw-nodes.yml"
}
