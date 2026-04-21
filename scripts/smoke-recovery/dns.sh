#!/bin/bash
# Auto-recovery: DNS (Tailscale MagicDNS + Docker container DNS)
# Sourced in cron mode by system-smoke-test.sh

tailscale_dns_fails=$(cat "$FAIL_COUNT_DIR/tailscale-dns.count" 2>/dev/null || echo "0")
docker_dns_fails=$(cat "$FAIL_COUNT_DIR/docker-dns.count" 2>/dev/null || echo "0")

# If Tailscale MagicDNS is failing to resolve, restart tailscaled
if [[ "$tailscale_dns_fails" -ge 3 ]]; then
    if ! check_circuit_breaker; then
        echo "[${NOW}] Circuit breaker tripped — skipping Tailscale auto-recovery" >> "$LOG_FILE"
    else
        send_alert "AUTO-RECOVERY: Tailscale MagicDNS not resolving for 15+ min — restarting tailscaled"
        sudo systemctl restart tailscaled 2>/dev/null || true
        sleep 10
        if dig +short +time=3 google.com @100.100.100.100 >/dev/null 2>&1; then
            send_alert "AUTO-RECOVERY SUCCESS: Tailscale MagicDNS resolving after restart"
            echo "0" > "$FAIL_COUNT_DIR/tailscale-dns.count"
            echo "up" > "$STATE_DIR/tailscale-dns.status"
        else
            send_alert "AUTO-RECOVERY FAILED: Tailscale MagicDNS still not resolving"
        fi
    fi
fi

# If Docker container DNS is broken for 3+ checks (15 min), restart affected containers
if [[ "$docker_dns_fails" -ge 3 ]]; then
    if ! check_circuit_breaker; then
        echo "[${NOW}] Circuit breaker tripped — skipping Docker DNS auto-recovery" >> "$LOG_FILE"
    else
        send_alert "AUTO-RECOVERY: Docker container DNS broken for 15+ min — restarting OpenClaw gateway"
        timed_ssh 15 ${HEAVY_HOST} "cd /mnt/external/openclaw && docker compose restart openclaw-gateway" 2>/dev/null
        sleep 10
        if timed_ssh 5 ${HEAVY_HOST} docker exec openclaw-openclaw-gateway-1 sh -c "getent hosts google.com" >/dev/null 2>&1; then
            send_alert "AUTO-RECOVERY SUCCESS: Docker container DNS restored after gateway restart"
            echo "0" > "$FAIL_COUNT_DIR/docker-dns.count"
            echo "up" > "$STATE_DIR/docker-dns.status"
        else
            send_alert "AUTO-RECOVERY FAILED: Docker container DNS still broken — may need daemon restart"
        fi
    fi
fi
