#!/bin/bash
# Checks: Cloudflare tunnel reachability + Keepalived HA VIP

check_cloudflared() {
    # CF Access returns 302 redirect; both 200 and 302 mean tunnel is reachable
    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
        -H "CF-Access-Client-Id: ${CF_ACCESS_CLIENT_ID:-}" \
        -H "CF-Access-Client-Secret: ${CF_ACCESS_CLIENT_SECRET:-}" \
        https://mc.siliconsentiments.work 2>/dev/null)
    if [ "$http_code" = "200" ] || [ "$http_code" = "302" ]; then
        check_service "cloudflared" "up"
    else
        check_service "cloudflared" "down" "Tunnel unreachable (HTTP $http_code)"
    fi
}

check_keepalived() {
    local s0_active s1_active
    s0_active=$(timed_ssh 5 slave0 "systemctl is-active keepalived" 2>/dev/null || echo "inactive")
    s1_active=$(timed_ssh 5 slave1 "systemctl is-active keepalived" 2>/dev/null || echo "inactive")

    if [[ "$s0_active" != "active" && "$s1_active" != "active" ]]; then
        check_service "keepalived" "down" "Both nodes keepalived down"
        return
    fi

    if [[ "$s0_active" != "active" || "$s1_active" != "active" ]]; then
        local down_node="slave0"
        [[ "$s0_active" == "active" ]] && down_node="slave1"
        check_service "keepalived" "degraded" "$down_node keepalived not running"
        return
    fi

    local s0_has_vip s1_has_vip
    s0_has_vip=$(timed_ssh 5 slave0 "ip -4 addr show eth0 | grep -c 192.168.0.53" 2>/dev/null | tr -d '[:space:]')
    s1_has_vip=$(timed_ssh 5 slave1 "ip -4 addr show eth0 | grep -c 192.168.0.53" 2>/dev/null | tr -d '[:space:]')
    : "${s0_has_vip:=0}" "${s1_has_vip:=0}"

    if [[ "$s0_has_vip" -gt 0 && "$s1_has_vip" -gt 0 ]]; then
        check_service "keepalived" "down" "SPLIT-BRAIN: VIP on both nodes"
        return
    fi

    if [[ "$s0_has_vip" -gt 0 || "$s1_has_vip" -gt 0 ]]; then
        check_service "keepalived" "up"
    else
        check_service "keepalived" "degraded" "VIP 192.168.0.53 not found on either node"
    fi
}
