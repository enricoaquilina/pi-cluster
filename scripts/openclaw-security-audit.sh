#!/bin/bash
# OpenClaw Security Best Practices Audit
# Checks secrets, bindings, memory limits, permissions, and more.
# Usage: bash scripts/openclaw-security-audit.sh
#        make security-audit

set -uo pipefail

PASS=0
FAIL=0
WARN=0
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOMELAB_DIR="$(dirname "$SCRIPT_DIR")"

pass() { PASS=$((PASS + 1)); echo "  PASS  $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL  $1: $2"; }
warn() { WARN=$((WARN + 1)); echo "  WARN  $1: $2"; }

echo "=== OpenClaw Security Audit ==="
date
echo ""

# 1. No secrets in tracked git files
echo "1. Secrets in tracked files"
if grep -rn --include='*.py' --include='*.sh' --include='*.yml' --include='*.yaml' \
    -E '(TELEGRAM_BOT_TOKEN|MC_API_KEY|OPENROUTER_API_KEY)\s*=\s*"?[a-zA-Z0-9_:-]{20,}' \
    "$HOMELAB_DIR/scripts/" "$HOMELAB_DIR/templates/" "$HOMELAB_DIR/playbooks/" 2>/dev/null \
    | grep -v '.env.cluster' | grep -v 'os.environ.get' | grep -v 'environ.get'; then
    fail "Hardcoded secrets" "found in tracked files"
else
    pass "No hardcoded secrets in tracked files"
fi

# 2. Service port bindings
echo "2. Port bindings"
if command -v ss > /dev/null 2>&1; then
    # MongoDB should be on 127.0.0.1 (check local address column only)
    mongo_bind=$(ss -tlnp 2>/dev/null | awk '/:27017 /{print $4}')
    if echo "$mongo_bind" | grep -q '127.0.0.1'; then
        pass "MongoDB bound to localhost ($mongo_bind)"
    elif [ -n "$mongo_bind" ]; then
        fail "MongoDB binding" "$mongo_bind (should be 127.0.0.1:27017)"
    else
        warn "MongoDB binding" "port 27017 not listening"
    fi

    # Gateway should be listening on LAN (0.0.0.0 or specific IP)
    gw_bind=$(ss -tlnp 2>/dev/null | awk '/:18789 /{print $4}')
    if echo "$gw_bind" | grep -qE '0\.0\.0\.0|192\.168\.0\.5'; then
        pass "Gateway bound to LAN ($gw_bind)"
    elif [ -n "$gw_bind" ]; then
        warn "Gateway binding" "$gw_bind (expected 0.0.0.0 or LAN IP)"
    else
        warn "Gateway binding" "port 18789 not listening"
    fi
else
    warn "Port bindings" "ss not available, skipping"
fi

# 3. Container memory limits
echo "3. Container memory limits"
if command -v docker > /dev/null 2>&1; then
    for container in openclaw-openclaw-gateway-1 openclaw-openclaw-cli-1 mongodb; do
        mem_limit=$(docker inspect "$container" --format '{{.HostConfig.Memory}}' 2>/dev/null)
        if [ -n "$mem_limit" ] && [ "$mem_limit" != "0" ]; then
            mem_mb=$((mem_limit / 1024 / 1024))
            pass "Memory limit $container: ${mem_mb}MB"
        elif docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${container}$"; then
            # Check if kernel supports cgroups memory limits
            if ! grep -q 'cgroup_memory=1' /proc/cmdline 2>/dev/null && [ "$(uname -m)" = "aarch64" ]; then
                warn "Memory limit $container" "kernel cgroups not enabled (add cgroup_memory=1 cgroup_enable=memory to /boot/firmware/cmdline.txt)"
            else
                fail "Memory limit $container" "no limit set"
            fi
        fi
    done
else
    warn "Container limits" "docker not available, skipping"
fi

# 4. File permissions on sensitive files
echo "4. File permissions"
ENV_FILE="$SCRIPT_DIR/.env.cluster"
if [ -f "$ENV_FILE" ]; then
    perms=$(stat -c '%a' "$ENV_FILE" 2>/dev/null)
    if [ "$perms" = "600" ]; then
        pass ".env.cluster permissions (600)"
    else
        fail ".env.cluster permissions" "is $perms, should be 600"
    fi
else
    warn ".env.cluster" "file not found"
fi

PAIRED_JSON="/home/enrico/.openclaw/devices/paired.json"
if [ -f "$PAIRED_JSON" ]; then
    perms=$(stat -c '%a' "$PAIRED_JSON" 2>/dev/null)
    if [ "$perms" = "600" ]; then
        pass "paired.json permissions (600)"
    else
        fail "paired.json permissions" "is $perms, should be 600"
    fi
else
    warn "paired.json" "file not found"
fi

# 5. Docker containers not running as root (where avoidable)
echo "5. Container user"
if command -v docker > /dev/null 2>&1; then
    for container in mongodb openclaw-openclaw-gateway-1 openclaw-openclaw-cli-1; do
        user=$(docker inspect "$container" --format '{{.Config.User}}' 2>/dev/null)
        if [ -n "$user" ] && [ "$user" != "0" ] && [ "$user" != "root" ] && [ "$user" != "0:0" ]; then
            pass "$container runs as non-root ($user)"
        elif docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${container}$"; then
            warn "$container user" "running as root (acceptable for some containers)"
        fi
    done
fi

# 6. SSH password auth disabled
echo "6. SSH config"
if grep -q "^PasswordAuthentication no" /etc/ssh/sshd_config 2>/dev/null; then
    pass "SSH password auth disabled"
else
    if grep -q "^PasswordAuthentication yes" /etc/ssh/sshd_config 2>/dev/null; then
        fail "SSH password auth" "enabled (should be disabled)"
    else
        warn "SSH password auth" "not explicitly set"
    fi
fi

# 7. Swap usage
echo "7. Swap usage"
swap_used=$(free -m 2>/dev/null | awk '/Swap/ {print $3}')
if [ -n "$swap_used" ]; then
    if [ "$swap_used" -lt 100 ]; then
        pass "Swap usage: ${swap_used}MB (< 100MB)"
    else
        warn "Swap usage" "${swap_used}MB (> 100MB threshold)"
    fi
fi

# 8. No world-readable sensitive files
echo "8. World-readable sensitive files"
world_readable=0
for f in "$ENV_FILE" "$PAIRED_JSON" /home/enrico/.ssh/id_ed25519 /home/enrico/.ssh/id_rsa /home/enrico/homelab/secrets/*.yml; do
    if [ -f "$f" ]; then
        perms=$(stat -c '%a' "$f" 2>/dev/null)
        last_digit="${perms: -1}"
        if [ "$last_digit" -ge 4 ] 2>/dev/null; then
            fail "World-readable" "$f (perms: $perms)"
            world_readable=$((world_readable + 1))
        fi
    fi
done
if [ "$world_readable" -eq 0 ]; then
    pass "No world-readable sensitive files"
fi

# Summary
echo ""
echo "=== Security Audit Results ==="
echo "Passed:   $PASS"
echo "Failed:   $FAIL"
echo "Warnings: $WARN"
echo ""

if [ "$FAIL" -gt 0 ]; then
    echo "RESULT: FAIL — $FAIL issue(s) need attention"
    exit 1
else
    echo "RESULT: PASS"
    exit 0
fi
