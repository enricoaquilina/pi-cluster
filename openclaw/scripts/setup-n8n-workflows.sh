#!/bin/bash
set -euo pipefail

# Deploy n8n workflows for polybot monitoring.
# Requires N8N_API_KEY and OPENCLAW_GATEWAY_TOKEN env vars.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKFLOW_DIR="$SCRIPT_DIR/../n8n-workflows"
N8N_URL="${N8N_BASE_URL:-http://localhost:5678}"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# Load env from openclaw .env if vars not set
if [ -z "${N8N_API_KEY:-}" ] || [ -z "${OPENCLAW_GATEWAY_TOKEN:-}" ]; then
    ENV_FILE="/mnt/external/openclaw/.env"
    if [ -f "$ENV_FILE" ]; then
        info "Loading env from $ENV_FILE"
        set -a
        source "$ENV_FILE"
        set +a
    else
        error "N8N_API_KEY and OPENCLAW_GATEWAY_TOKEN must be set (or exist in $ENV_FILE)"
    fi
fi

# Check n8n is reachable
if ! curl -sf "$N8N_URL/healthz" > /dev/null 2>&1; then
    error "n8n is not reachable at $N8N_URL"
fi
info "n8n is healthy at $N8N_URL"

# Step 1: Add polybot-data mount to n8n if not already present
N8N_COMPOSE="/home/enrico/docker-compose-n8n.yml"
if ! grep -q "polybot-data" "$N8N_COMPOSE" 2>/dev/null; then
    info "Adding polybot-data volume mount to n8n docker-compose..."
    # Insert the mount line after the existing volumes entry
    sed -i '/\/home\/node\/.n8n$/a\      - /mnt/external/polymarket-bot/data:/polybot-data:ro' "$N8N_COMPOSE"
    info "Mount added. n8n will need a restart."
    NEED_N8N_RESTART=true
else
    info "polybot-data mount already present in n8n compose."
    NEED_N8N_RESTART=false
fi

# Step 2: Add OPENCLAW_GATEWAY_TOKEN env to n8n if not present
if ! grep -q "OPENCLAW_GATEWAY_TOKEN" "$N8N_COMPOSE" 2>/dev/null; then
    info "Adding OPENCLAW_GATEWAY_TOKEN env to n8n..."
    sed -i "/N8N_RUNNERS_ENABLED=true/a\\      - OPENCLAW_GATEWAY_TOKEN=${OPENCLAW_GATEWAY_TOKEN}" "$N8N_COMPOSE"
    NEED_N8N_RESTART=true
fi

# Step 3: Restart n8n if config changed
if [ "$NEED_N8N_RESTART" = true ]; then
    info "Restarting n8n container..."
    cd /home/enrico
    docker compose -f docker-compose-n8n.yml up -d n8n-production
    sleep 10
    if ! curl -sf "$N8N_URL/healthz" > /dev/null 2>&1; then
        error "n8n failed to restart healthy"
    fi
    info "n8n restarted successfully."
fi

# Step 4: Import workflows
import_workflow() {
    local file="$1"
    local name
    name=$(python3 -c "import json; print(json.load(open('$file'))['name'])")

    # Check if workflow already exists
    local existing
    existing=$(curl -sf -H "X-N8N-API-KEY: $N8N_API_KEY" "$N8N_URL/api/v1/workflows" 2>/dev/null || echo '{"data":[]}')
    local existing_id
    existing_id=$(echo "$existing" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for w in data.get('data', []):
    if w.get('name') == '$name':
        print(w['id'])
        break
" 2>/dev/null || echo "")

    if [ -n "$existing_id" ]; then
        info "Updating existing workflow '$name' (ID: $existing_id)..."
        curl -sf -X PUT \
            -H "X-N8N-API-KEY: $N8N_API_KEY" \
            -H "Content-Type: application/json" \
            -d @"$file" \
            "$N8N_URL/api/v1/workflows/$existing_id" > /dev/null
        # Activate
        curl -sf -X PATCH \
            -H "X-N8N-API-KEY: $N8N_API_KEY" \
            -H "Content-Type: application/json" \
            -d '{"active": true}' \
            "$N8N_URL/api/v1/workflows/$existing_id" > /dev/null
    else
        info "Creating workflow '$name'..."
        local result
        result=$(curl -sf -X POST \
            -H "X-N8N-API-KEY: $N8N_API_KEY" \
            -H "Content-Type: application/json" \
            -d @"$file" \
            "$N8N_URL/api/v1/workflows")
        local new_id
        new_id=$(echo "$result" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])" 2>/dev/null || echo "")
        if [ -n "$new_id" ]; then
            # Activate
            curl -sf -X PATCH \
                -H "X-N8N-API-KEY: $N8N_API_KEY" \
                -H "Content-Type: application/json" \
                -d '{"active": true}' \
                "$N8N_URL/api/v1/workflows/$new_id" > /dev/null
            info "Created and activated workflow '$name' (ID: $new_id)"
        else
            error "Failed to create workflow '$name'"
        fi
    fi
}

for wf in "$WORKFLOW_DIR"/*.json; do
    [ -f "$wf" ] || continue
    import_workflow "$wf"
done

echo ""
info "All workflows deployed. Check $N8N_URL for status."
