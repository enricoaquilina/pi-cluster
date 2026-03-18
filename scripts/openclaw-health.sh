#!/bin/bash
# OpenClaw Cluster Health Check
# Runs from master, checks all nodes and services
# Usage: bash scripts/openclaw-health.sh

THRESHOLD_RAM=85

echo "=== OpenClaw Cluster Health ==="
echo "$(date)"
echo ""

# Check node connectivity via OpenClaw
echo "--- Node Status ---"
if command -v openclaw &>/dev/null; then
    for node in build light; do
        if openclaw nodes describe --node "$node" 2>/dev/null | grep -q "connected"; then
            echo "$node: CONNECTED"
        else
            echo "$node: OFFLINE"
        fi
    done
else
    echo "openclaw CLI not available — skipping node status"
fi

echo ""

# Check RAM per node via SSH
echo "--- RAM Usage ---"
for host in slave0 slave1; do
    RAM_USED=$(ssh -o ConnectTimeout=5 "$host" "free | awk '/^Mem:/ {printf \"%.0f\", \$3/\$2 * 100}'" 2>/dev/null)
    if [ -z "$RAM_USED" ]; then
        echo "$host: SSH FAILED"
    elif [ "$RAM_USED" -gt "$THRESHOLD_RAM" ]; then
        echo "$host: ${RAM_USED}% OVER THRESHOLD"
    else
        echo "$host: ${RAM_USED}% OK"
    fi
done

echo ""

# Check NFS mounts on slaves
echo "--- NFS Mounts ---"
for host in slave0 slave1; do
    for mount_path in /opt/workspace /mnt/external; do
        NFS_OK=$(ssh -o ConnectTimeout=5 "$host" "mountpoint -q $mount_path && echo OK || echo FAILED" 2>/dev/null)
        if [ -z "$NFS_OK" ]; then
            echo "$host $mount_path: SSH FAILED"
        else
            echo "$host $mount_path: $NFS_OK"
        fi
    done
done

echo ""

# Check active subagents
echo "--- Active Subagents ---"
if command -v openclaw &>/dev/null; then
    openclaw subagents list 2>/dev/null || echo "No active subagents"
else
    echo "openclaw CLI not available"
fi

echo ""

# Check gateway process (Docker)
echo "--- Gateway ---"
if docker ps --filter name=openclaw --format '{{.Names}}: {{.Status}}' 2>/dev/null | head -5; then
    :
else
    echo "Docker not available or gateway not running"
fi

echo ""
echo "=== Health Check Complete ==="
