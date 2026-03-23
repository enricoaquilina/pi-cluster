#!/bin/bash
# OpenClaw Disaster Recovery Validation
# Verifies backups exist, are recent, and contain valid data.
# Usage: bash scripts/openclaw-dr-test.sh
#        make dr-test
# Weekly cron: Sunday 4am

set -uo pipefail

BACKUP_ROOT="/mnt/external/backups"
REMOTE_HOST="192.168.0.5"
REMOTE_DIR="/home/enrico/backups"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP_DIR=$(mktemp -d /tmp/openclaw-dr-test.XXXXXX)

PASS=0
FAIL=0
TESTS=()

pass() { PASS=$((PASS + 1)); TESTS+=("PASS  $1"); echo "  PASS  $1"; }
fail() { FAIL=$((FAIL + 1)); TESTS+=("FAIL  $1: $2"); echo "  FAIL  $1: $2"; }

cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

echo "=== OpenClaw DR Validation ==="
date
echo ""

# 1. Backup exists and is recent (<24h)
echo "1. Backup freshness"
LATEST=$(find "$BACKUP_ROOT" -name "backup-*.tar.gz" -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1)
if [ -z "$LATEST" ]; then
    fail "Backup exists" "no backups found in $BACKUP_ROOT"
else
    LATEST_FILE=$(echo "$LATEST" | awk '{print $2}')
    LATEST_TS=$(echo "$LATEST" | awk '{print $1}' | cut -d. -f1)
    NOW=$(date +%s)
    AGE_HOURS=$(( (NOW - LATEST_TS) / 3600 ))

    if [ "$AGE_HOURS" -lt 24 ]; then
        pass "Backup recent: $(basename "$LATEST_FILE") (${AGE_HOURS}h old)"
    else
        fail "Backup freshness" "$(basename "$LATEST_FILE") is ${AGE_HOURS}h old (>24h)"
    fi
fi

# 2-6. Extract and validate backup contents
echo "2. Backup contents"
if [ -n "${LATEST_FILE:-}" ] && [ -f "$LATEST_FILE" ]; then
    tar -xzf "$LATEST_FILE" -C "$TMP_DIR" 2>/dev/null
    EXTRACTED=$(find "$TMP_DIR" -maxdepth 1 -mindepth 1 -type d | head -1)

    if [ -z "$EXTRACTED" ]; then
        fail "Backup extraction" "could not extract $LATEST_FILE"
    else
        # paired.json — valid JSON with expected node count
        PAIRED="$EXTRACTED/gateway/paired.json"
        if [ -f "$PAIRED" ]; then
            node_count=$(python3 -c "
import json, sys
with open('$PAIRED') as f:
    data = json.load(f)
nodes = data if isinstance(data, list) else data.get('devices', data.get('nodes', []))
print(len(nodes))
" 2>/dev/null)
            if [ -n "$node_count" ] && [ "$node_count" -ge 4 ]; then
                pass "paired.json valid ($node_count nodes)"
            elif [ -n "$node_count" ]; then
                fail "paired.json" "only $node_count nodes (expected >= 4)"
            else
                fail "paired.json" "invalid JSON"
            fi
        else
            fail "paired.json" "not found in backup"
        fi

        # openclaw.json — valid JSON
        OC_JSON="$EXTRACTED/gateway/openclaw.json"
        if [ -f "$OC_JSON" ]; then
            if python3 -c "import json; json.load(open('$OC_JSON'))" 2>/dev/null; then
                pass "openclaw.json valid JSON"
            else
                fail "openclaw.json" "invalid JSON"
            fi
        else
            fail "openclaw.json" "not found in backup"
        fi

        # MC SQL dump — check for PostgreSQL syntax
        MC_SQL="$EXTRACTED/mc/missioncontrol.sql"
        if [ -f "$MC_SQL" ] && [ -s "$MC_SQL" ]; then
            if head -20 "$MC_SQL" | grep -qiE '(pg_dump|postgresql|create table|set )'; then
                pass "MC SQL dump valid ($(wc -l < "$MC_SQL") lines)"
            else
                fail "MC SQL dump" "does not look like valid PostgreSQL dump"
            fi
        else
            fail "MC SQL dump" "not found or empty"
        fi

        # Node identity files
        echo "3. Node identities"
        for node in master-operator master-node slave0 slave1 heavy; do
            ID_FILE="$EXTRACTED/identities/$node.json"
            if [ -f "$ID_FILE" ] && [ -s "$ID_FILE" ]; then
                if python3 -c "import json; json.load(open('$ID_FILE'))" 2>/dev/null; then
                    pass "Identity $node valid"
                else
                    fail "Identity $node" "invalid JSON"
                fi
            else
                fail "Identity $node" "not found or empty"
            fi
        done

        # Dispatch log database
        echo "4. Dispatch log"
        DISPATCH_DB="$EXTRACTED/dispatch/openclaw-dispatch-log.db"
        if [ -f "$DISPATCH_DB" ]; then
            tables=$(sqlite3 "$DISPATCH_DB" ".tables" 2>/dev/null)
            if echo "$tables" | grep -q "dispatch_log"; then
                row_count=$(sqlite3 "$DISPATCH_DB" "SELECT COUNT(*) FROM dispatch_log;" 2>/dev/null)
                pass "Dispatch log DB valid ($row_count rows)"
            else
                fail "Dispatch log DB" "missing dispatch_log table"
            fi
        else
            fail "Dispatch log DB" "not found in backup"
        fi
    fi
else
    fail "Backup extraction" "no backup file to extract"
fi

# 5. Off-site backup on heavy matches local
echo "5. Off-site backup"
if [ -n "${LATEST_FILE:-}" ]; then
    LOCAL_NAME=$(basename "$LATEST_FILE")
    REMOTE_EXISTS=$(ssh -o ConnectTimeout=5 -o BatchMode=yes "$REMOTE_HOST" \
        "test -f $REMOTE_DIR/$LOCAL_NAME && stat -c '%s' $REMOTE_DIR/$LOCAL_NAME" 2>/dev/null)
    LOCAL_SIZE=$(stat -c '%s' "$LATEST_FILE" 2>/dev/null)

    if [ -n "$REMOTE_EXISTS" ] && [ "$REMOTE_EXISTS" = "$LOCAL_SIZE" ]; then
        pass "Off-site backup matches local ($LOCAL_NAME)"
    elif [ -n "$REMOTE_EXISTS" ]; then
        fail "Off-site backup" "size mismatch: local=$LOCAL_SIZE remote=$REMOTE_EXISTS"
    else
        fail "Off-site backup" "$LOCAL_NAME not found on heavy"
    fi
fi

# Summary
echo ""
echo "=== DR Validation Results ==="
echo "Passed: $PASS"
echo "Failed: $FAIL"
echo "Total:  $((PASS + FAIL))"

if [ "$FAIL" -gt 0 ]; then
    echo ""
    echo "Failures:"
    for t in "${TESTS[@]}"; do
        echo "$t" | grep "^FAIL" || true
    done

    # Send Telegram alert on failure if env available
    ENV_FILE="$SCRIPT_DIR/.env.cluster"
    if [ -f "$ENV_FILE" ]; then
        # shellcheck source=/dev/null
        source "$ENV_FILE"
    fi
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
        failures=$(printf '%s\n' "${TESTS[@]}" | grep "^FAIL" | head -5)
        curl -sf -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${TELEGRAM_CHAT_ID}" \
            -d "text=🔴 *DR Validation Failed*
Passed: $PASS / Failed: $FAIL
$failures" \
            -d "parse_mode=Markdown" > /dev/null 2>&1
    fi
fi

echo ""
echo "=== DR Validation Complete ==="
[ "$FAIL" -eq 0 ]
