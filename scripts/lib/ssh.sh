#!/bin/bash
# Cluster SSH helper — standardizes connection options across all scripts.
# Usage: source scripts/lib/ssh.sh
#   cluster_ssh <host> <command>
#   cluster_ssh heavy "docker ps"

cluster_ssh() {
    local host="$1"; shift
    ssh -o ConnectTimeout=5 -o BatchMode=yes "$host" "$@"
}
