#!/bin/bash
# OpenClaw Pre-Deploy Validation
# Catches configuration drift, missing env vars, and token mismatches
# BEFORE deploying. Run manually, from openclaw-update.sh, or in CI.
#
# Usage:
#   openclaw-preflight.sh              # Run all checks (interactive)
#   openclaw-preflight.sh --ci         # Non-interactive, strict exit codes
#   openclaw-preflight.sh --fix-hints  # Show fix commands for failures
#
# Exit codes: 0 = all pass, 1 = failures found

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/ssh.sh
source "$SCRIPT_DIR/lib/ssh.sh"
COMPOSE_DIR="${OPENCLAW_COMPOSE_DIR:-/home/enrico/openclaw}"
COMPOSE_FILE="$COMPOSE_DIR/docker-compose.yml"
ENV_FILE="$COMPOSE_DIR/.env"
ENV_CLUSTER="${OPENCLAW_ENV_CLUSTER:-$SCRIPT_DIR/.env.cluster}"
GATEWAY="${OPENCLAW_GATEWAY_CONTAINER:-openclaw-openclaw-gateway-1}"
SKIP_RUNTIME="${OPENCLAW_PREFLIGHT_SKIP_RUNTIME:-false}"

[[ "${1:-}" == "--ci" ]] && _HARNESS_CI=true
[[ "${1:-}" == "--fix-hints" ]] && _HARNESS_FIX_HINTS=true
# shellcheck source=scripts/lib/test-harness.sh
source "$SCRIPT_DIR/lib/test-harness.sh"

# ── 1. Docker Compose Syntax ────────────────────────────────────────────────
echo "1. Docker compose validation"
if [ ! -f "$COMPOSE_FILE" ]; then
    fail "docker-compose.yml" "not found at $COMPOSE_FILE"
else
    if (cd "$COMPOSE_DIR" && docker compose config -q 2>/dev/null); then
        pass "docker-compose.yml syntax valid"
    else
        compose_err=$(cd "$COMPOSE_DIR" && docker compose config 2>&1 | tail -3)
        fail "docker-compose.yml" "invalid: $compose_err"
    fi
fi

# ── 2. Required Env Vars in .env ─────────────────────────────────────────────
echo "2. Required env vars"

# These are the env vars that cause silent failures if missing
REQUIRED_KEYS=(
    OPENCLAW_GATEWAY_TOKEN
    OPENROUTER_API_KEY
    GOOGLE_AI_API_KEY
)

# These are optional but worth warning about
OPTIONAL_KEYS=(
    ANTHROPIC_API_KEY
    DEEPSEEK_API_KEY
    MOONSHOT_API_KEY
    ZAI_API_KEY
    TAVILY_API_KEY
)

if [ ! -f "$ENV_FILE" ]; then
    fail ".env file" "not found at $ENV_FILE"
else
    for key in "${REQUIRED_KEYS[@]}"; do
        val=$(grep "^${key}=" "$ENV_FILE" 2>/dev/null | cut -d= -f2-)
        if [ -n "$val" ]; then
            pass "$key present"
        else
            fail "$key" "missing or empty in $ENV_FILE" \
                "echo '${key}=<value>' >> $ENV_FILE"
        fi
    done
    for key in "${OPTIONAL_KEYS[@]}"; do
        val=$(grep "^${key}=" "$ENV_FILE" 2>/dev/null | cut -d= -f2-)
        if [ -n "$val" ]; then
            pass "$key present"
        else
            warn "$key" "missing (fallback models may not work)"
        fi
    done
fi

# ── 3. Env Var Sync (.env vs .env.cluster) ──────────────────────────────────
echo "3. Env var sync"
if [ -f "$ENV_FILE" ] && [ -f "$ENV_CLUSTER" ]; then
    # Check that shared keys have matching values
    for key in OPENCLAW_GATEWAY_TOKEN OPENROUTER_API_KEY; do
        val_env=$(grep "^${key}=" "$ENV_FILE" 2>/dev/null | cut -d= -f2-)
        val_cluster=$(grep "^${key}=" "$ENV_CLUSTER" 2>/dev/null | cut -d= -f2-)
        if [ -z "$val_env" ] || [ -z "$val_cluster" ]; then
            continue  # Already caught in check 2
        fi
        if [ "$val_env" = "$val_cluster" ]; then
            pass "$key in sync (.env = .env.cluster)"
        else
            fail "$key drift" ".env and .env.cluster have different values" \
                "Update one to match the other"
        fi
    done
else
    warn "env sync" "cannot check — one or both .env files missing"
fi

# ── 4. Token Consistency (gateway config vs env) ────────────────────────────
echo "4. Token consistency"
env_token=$(grep "^OPENCLAW_GATEWAY_TOKEN=" "$ENV_FILE" 2>/dev/null | cut -d= -f2-)

# Check gateway config if container is running
if [[ "$SKIP_RUNTIME" == "true" ]]; then
    warn "Token runtime" "skipped (SKIP_RUNTIME=true)"
elif docker ps --filter "name=$GATEWAY" --format '{{.Names}}' 2>/dev/null | grep -q "$GATEWAY"; then
    config_token=$(docker exec "$GATEWAY" python3 -c "
import json
with open('/home/node/.openclaw/openclaw.json') as f:
    print(json.load(f)['gateway']['auth']['token'])
" 2>/dev/null)
    container_token=$(docker exec "$GATEWAY" printenv OPENCLAW_GATEWAY_TOKEN 2>/dev/null)

    if [ -n "$env_token" ] && [ -n "$config_token" ] && [ "$env_token" = "$config_token" ]; then
        pass "Gateway config token matches .env"
    elif [ -n "$config_token" ]; then
        fail "Token drift" "openclaw.json='${config_token:0:8}...' .env='${env_token:0:8}...'"
    fi

    if [ -n "$env_token" ] && [ -n "$container_token" ] && [ "$env_token" = "$container_token" ]; then
        pass "Container env token matches .env"
    elif [ -n "$container_token" ]; then
        fail "Token drift" "container env='${container_token:0:8}...' .env='${env_token:0:8}...'" \
            "Recreate container: cd $COMPOSE_DIR && docker compose up -d"
    fi
else
    warn "Token check" "gateway container not running — skipping runtime checks"
fi

# Check node tokens via SSH (non-blocking, skip if unreachable)
if [[ "$SKIP_RUNTIME" == "true" ]]; then
    : # Already warned above
elif [ -n "$env_token" ]; then
    for node in "master:control" "slave0:build" "slave1:light" "heavy:heavy"; do
        IFS=: read -r host name <<< "$node"
        node_token=$(cluster_ssh "$host" \
            "grep 'OPENCLAW_GATEWAY_TOKEN=' /etc/systemd/system/openclaw-node.service 2>/dev/null | cut -d= -f3" 2>/dev/null)
        if [ -z "$node_token" ]; then
            continue  # Node unreachable or no service file
        fi
        if [ "$node_token" = "$env_token" ]; then
            pass "Node $name token matches"
        else
            fail "Node $name token drift" "node='${node_token:0:8}...' expected='${env_token:0:8}...'" \
                "ssh $host 'sudo sed -i \"s/OPENCLAW_GATEWAY_TOKEN=.*/OPENCLAW_GATEWAY_TOKEN=$env_token/\" /etc/systemd/system/openclaw-node.service && sudo systemctl daemon-reload && sudo systemctl restart openclaw-node'"
        fi
    done
fi

# ── 5. Docker Image Patch ───────────────────────────────────────────────────
echo "5. Image patch integrity"
if [[ "$SKIP_RUNTIME" == "true" ]]; then
    warn "Patch check" "skipped (SKIP_RUNTIME=true)"
elif docker ps --filter "name=$GATEWAY" --format '{{.Names}}' 2>/dev/null | grep -q "$GATEWAY"; then
    patch_count=$(docker exec "$GATEWAY" grep -c "^[[:space:]]*group &&" \
        /app/extensions/whatsapp/src/inbound/monitor.ts 2>/dev/null)
    if [ "${patch_count:-1}" -eq 0 ]; then
        pass "Echo detection patch applied in running container"
    else
        fail "Echo detection patch" "group && still present — echo loops possible" \
            "cd $COMPOSE_DIR && docker compose build && docker compose up -d"
    fi
else
    warn "Patch check" "gateway container not running — skipping"
fi

# ── 6. File Permissions ─────────────────────────────────────────────────────
echo "6. File permissions"
for f in "$ENV_FILE" "$ENV_CLUSTER"; do
    [ ! -f "$f" ] && continue
    perms=$(stat -c '%a' "$f" 2>/dev/null)
    name=$(basename "$f")
    if [ "$perms" = "600" ]; then
        pass "Perms $name (600)"
    else
        fail "Perms $name" "$perms (should be 600)" \
            "chmod 600 $f"
    fi
done

# ── 7. Docker Compose Env Var Coverage ──────────────────────────────────────
echo "7. Compose env var coverage"
if [ -f "$COMPOSE_FILE" ] && [ -f "$ENV_FILE" ]; then
    # Extract env vars referenced in docker-compose.yml
    compose_vars=$(grep -oP '\$\{([A-Z_]+):-' "$COMPOSE_FILE" | sed 's/${//;s/:-//' | sort -u)
    missing_count=0
    for var in $compose_vars; do
        # Skip vars with defaults that are intentionally empty
        case "$var" in
            OPENCLAW_ALLOW_INSECURE_PRIVATE_WS|OPENCLAW_BIND_IP|OPENCLAW_TZ) continue ;;
            CLAUDE_AI_SESSION_KEY|CLAUDE_WEB_SESSION_KEY|CLAUDE_WEB_COOKIE) continue ;;
        esac
        if ! grep -q "^${var}=" "$ENV_FILE" 2>/dev/null; then
            fail "$var" "referenced in docker-compose.yml but missing from .env"
            missing_count=$((missing_count + 1))
        fi
    done
    [ "$missing_count" -eq 0 ] && pass "All compose env vars covered in .env"
fi

# ── 8. MC Deployment Symlink ──────────────────────────────────────────────
echo "8. MC deployment symlink"
if [[ "$SKIP_RUNTIME" == "true" ]]; then
    warn "MC symlink" "skipped (SKIP_RUNTIME=true)"
elif ssh -o ConnectTimeout=3 -o BatchMode=yes heavy "test -L /home/enrico/mission-control" 2>/dev/null; then
    pass "MC deployed via symlink"
else
    fail "MC deployment" "not a symlink (risk of code drift)" \
        "ln -s /home/enrico/pi-cluster/mission-control /home/enrico/mission-control"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
test_summary
result=$?
if [ "$FAIL" -gt 0 ] && ! $_HARNESS_FIX_HINTS; then
    echo ""
    echo "Run with --fix-hints to see fix commands."
fi
exit $result
