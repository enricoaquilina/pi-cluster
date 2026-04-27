#!/bin/bash
# Post-deploy smoke check — critical services only
# Called by auto-deploy.sh after pulling new code.
# Exits 0 on pass, 1 on any critical service down.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[ -f "$SCRIPT_DIR/.env.cluster" ] && source "$SCRIPT_DIR/.env.cluster"

# shellcheck source=scripts/lib/smoke-common.sh
source "$SCRIPT_DIR/lib/smoke-common.sh"

# shellcheck source=scripts/smoke-checks/01-openclaw-gateway.sh
source "$SCRIPT_DIR/smoke-checks/01-openclaw-gateway.sh"
# shellcheck source=scripts/smoke-checks/03-databases.sh
source "$SCRIPT_DIR/smoke-checks/03-databases.sh"
# shellcheck source=scripts/smoke-checks/05-openclaw-nodes.sh
source "$SCRIPT_DIR/smoke-checks/05-openclaw-nodes.sh"
# shellcheck source=scripts/smoke-checks/08-dns.sh
source "$SCRIPT_DIR/smoke-checks/08-dns.sh"
# shellcheck source=scripts/smoke-checks/17-heartbeat-canary.sh
source "$SCRIPT_DIR/smoke-checks/17-heartbeat-canary.sh"

check_openclaw_gateway
check_mc_api
check_postgres
_fetch_openclaw_node_status
check_openclaw_master
check_openclaw_slave0
check_pihole
_fetch_heartbeat_canary
check_heartbeat_canary

CRITICAL_SVCS=(openclaw-gateway mission-control-api postgresql openclaw-master openclaw-slave0 pihole-dns heartbeat-canary)
FAILED=()
for svc in "${CRITICAL_SVCS[@]}"; do
    status="${RESULTS[$svc]:-unknown}"
    if [[ "$status" == "down" ]]; then
        FAILED+=("$svc")
    fi
done

if [[ ${#FAILED[@]} -gt 0 ]]; then
    echo "DEPLOY_SMOKE_FAIL: ${FAILED[*]}"
    exit 1
fi
echo "DEPLOY_SMOKE_OK"
exit 0
