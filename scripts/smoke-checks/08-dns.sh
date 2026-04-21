#!/bin/bash
# Checks: Pi-hole DNS, Docker container DNS, Tailscale MagicDNS

check_pihole() {
    if dig +short +time=2 @192.168.0.53 google.com >/dev/null 2>&1; then
        check_service "pihole-dns" "up"
    else
        check_service "pihole-dns" "down" "DNS resolution failed"
    fi
}

check_docker_dns() {
    local test_container
    test_container="mongodb"  # containers run on heavy
    if ! _ssh ${HEAVY_HOST} "docker ps -q" >/dev/null 2>&1; then
        check_service "docker-dns" "down" "No running containers"
        return
    fi
    if timed_ssh 8 ${HEAVY_HOST} "docker exec mongodb getent hosts google.com" >/dev/null 2>&1; then
        check_service "docker-dns" "up"
    else
        check_service "docker-dns" "down" "Container DNS failed (${test_container})"
    fi
}

check_tailscale_dns() {
    local ts_active=false
    grep -q "100.100.100.100" /etc/resolv.conf 2>/dev/null && ts_active=true

    if [[ "$ts_active" == true ]]; then
        # Tailscale MagicDNS is active — verify it resolves
        if ! dig +short +time=3 google.com @100.100.100.100 >/dev/null 2>&1; then
            check_service "tailscale-dns" "down" "Tailscale MagicDNS active but not resolving"
            return
        fi
        # Verify Pi-hole VIP is reachable through the chain
        if ! dig +short +time=3 google.com @192.168.0.53 >/dev/null 2>&1; then
            check_service "tailscale-dns" "degraded" "MagicDNS OK but Pi-hole VIP (192.168.0.53) unreachable"
            return
        fi
        check_service "tailscale-dns" "up"
    else
        # No Tailscale DNS — just check system resolver works
        if dig +short +time=3 google.com >/dev/null 2>&1; then
            check_service "tailscale-dns" "up"
        else
            check_service "tailscale-dns" "down" "System DNS not resolving"
        fi
    fi
}
