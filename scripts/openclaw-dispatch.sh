#!/bin/bash
# OpenClaw Task Dispatcher
# Routes a command to the best available node based on task type.
#
# Usage:
#   openclaw-dispatch.sh <task_type> <command>
#   openclaw-dispatch.sh coding "git status"
#   openclaw-dispatch.sh compute "python3 heavy_script.py"
#   openclaw-dispatch.sh any "echo hello"
#
# Task types: coding, research, compute, any

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TASK_TYPE="${1:-any}"
shift || { echo "Usage: $0 <task_type> <command>" >&2; exit 1; }
COMMAND="$*"

if [ -z "$COMMAND" ]; then
    echo "Usage: $0 <task_type> <command>" >&2
    exit 1
fi

# Select best node
NODE=$("$SCRIPT_DIR/openclaw-router.sh" "$TASK_TYPE")

if [ "$NODE" = "none" ]; then
    echo "ERROR: No suitable node available for task type '$TASK_TYPE'" >&2
    exit 1
fi

echo "Dispatching to '$NODE' (task: $TASK_TYPE)..." >&2

# Execute via gateway
docker exec openclaw-openclaw-gateway-1 openclaw nodes run \
    --node "$NODE" \
    --raw "$COMMAND" 2>&1 | grep -v "plugin.*mismatch" | grep -v "Config warnings" | grep -v "^│" | grep -v "^├" | grep -v "^$"
