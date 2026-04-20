#!/usr/bin/env bats
# Security tests for NFS configuration.
#
# Validates that NFS exports are locked down, secrets are not exposed,
# mount options are hardened, and failover scripts reflect the new topology.

setup() {
    REPO_DIR="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
}

@test "NFS mount options include timeo>=30" {
    grep -q "timeo=30" "$REPO_DIR/vars/openclaw-nodes.yml"
}

@test "NFS mount options include nolock" {
    grep -q "nolock" "$REPO_DIR/vars/openclaw-nodes.yml"
}

@test "no secrets in NFS-exported directories" {
    [ -d /mnt/data ]
    run grep -rl "API_KEY\|PASSWORD\|SECRET\|TOKEN" /mnt/data/ --include="*.env"
    [ "$status" -ne 0 ]
}

@test "UFW NFS rule is subnet-restricted (not open to Anywhere)" {
    run sudo ufw status
    if echo "$output" | grep -q "2049"; then
        ! echo "$output" | grep "2049" | grep -q "Anywhere"
    else
        # Port not open at all — also a failure (caught by migration test)
        false
    fi
}

@test "NFS exports template has no Tailscale subnet" {
    ! grep -q "100.64.0.0" "$REPO_DIR/templates/nfs-exports.j2"
}

@test "emergency-restore-master.sh references heavy as NFS primary" {
    grep -q "192.168.0.5" "$REPO_DIR/scripts/emergency-restore-master.sh"
}

@test "emergency-restore-master.sh handles NFS failover" {
    grep -qi "nfs\|export" "$REPO_DIR/scripts/emergency-restore-master.sh"
}

@test "nfs_server var points to heavy IP" {
    grep -q "nfs_server: 192.168.0.5" "$REPO_DIR/vars/openclaw-nodes.yml"
}
