#!/usr/bin/env bash
# Split-tunnel routing: only Discord traffic goes through the KeepSolid VPN.
# Deployed to both master and slave0 by Ansible.

set -euo pipefail

IFACE="keepsolid"
TABLE=100
FWMARK="0x10"
FWMASK="0x10"

# Static Discord CIDRs (Cloudflare ranges used by Discord)
DISCORD_CIDRS=(
  "162.159.0.0/16"
  "199.232.0.0/16"
)

# Discord hostnames to resolve dynamically
DISCORD_HOSTS=(
  "discord.com"
  "gateway.discord.gg"
  "cdn.discordapp.com"
  "discordapp.com"
  "discord.gg"
  "media.discordapp.net"
  "images-ext-1.discordapp.net"
  "discord-attachments-uploads-prd.storage.googleapis.com"
)

resolve_hosts() {
  local ips=()
  for host in "${DISCORD_HOSTS[@]}"; do
    while IFS= read -r ip; do
      [[ -n "$ip" ]] && ips+=("$ip/32")
    done < <(getent ahostsv4 "$host" 2>/dev/null | awk '{print $1}' | sort -u)
  done
  printf '%s\n' "${ips[@]}" | sort -u
}

get_all_cidrs() {
  {
    printf '%s\n' "${DISCORD_CIDRS[@]}"
    resolve_hosts
  } | sort -u
}

start() {
  # Ensure the interface is up
  if ! ip link show "$IFACE" &>/dev/null; then
    echo "ERROR: Interface $IFACE not found. Is wg-quick@keepsolid running?" >&2
    exit 1
  fi

  local gw
  gw=$(ip -4 addr show dev "$IFACE" | awk '/inet / {print $2}' | cut -d/ -f1)

  # Add routing table entry if not in rt_tables
  if ! grep -q "^${TABLE} " /etc/iproute2/rt_tables 2>/dev/null; then
    echo "${TABLE} discord_vpn" >> /etc/iproute2/rt_tables
  fi

  # Default route in custom table via the VPN interface
  ip route replace default dev "$IFACE" table "$TABLE"

  # Policy rule: marked packets use our custom table
  if ! ip rule show | grep -q "fwmark ${FWMARK}.*lookup ${TABLE}"; then
    ip rule add fwmark "${FWMARK}/${FWMASK}" table "$TABLE" priority 100
  fi

  # Mark Discord-bound packets (host-originated)
  for cidr in $(get_all_cidrs); do
    iptables -t mangle -C OUTPUT -d "$cidr" -j MARK --set-mark "${FWMARK}/${FWMASK}" 2>/dev/null ||
      iptables -t mangle -A OUTPUT -d "$cidr" -j MARK --set-mark "${FWMARK}/${FWMASK}"
  done

  # Mark Discord-bound packets from Docker containers (bridge networks)
  for cidr in $(get_all_cidrs); do
    iptables -t mangle -C PREROUTING -d "$cidr" -j MARK --set-mark "${FWMARK}/${FWMASK}" 2>/dev/null ||
      iptables -t mangle -A PREROUTING -d "$cidr" -j MARK --set-mark "${FWMARK}/${FWMASK}"
  done

  # NAT/MASQUERADE for traffic leaving via the VPN interface
  iptables -t nat -C POSTROUTING -o "$IFACE" -j MASQUERADE 2>/dev/null ||
    iptables -t nat -A POSTROUTING -o "$IFACE" -j MASQUERADE

  echo "Discord VPN split-tunnel routes configured."
}

stop() {
  # Remove mangle rules
  for chain in OUTPUT PREROUTING; do
    while iptables -t mangle -L "$chain" -n --line-numbers 2>/dev/null | grep -q "mark match"; do
      # Delete from the bottom up to avoid index shifts
      local line
      line=$(iptables -t mangle -L "$chain" -n --line-numbers 2>/dev/null | grep "mark match" | tail -1 | awk '{print $1}')
      [[ -n "$line" ]] && iptables -t mangle -D "$chain" "$line" || break
    done
  done

  # Remove NAT rule
  iptables -t nat -D POSTROUTING -o "$IFACE" -j MASQUERADE 2>/dev/null || true

  # Remove policy rule
  while ip rule del fwmark "${FWMARK}/${FWMASK}" table "$TABLE" 2>/dev/null; do :; done

  # Flush custom routing table
  ip route flush table "$TABLE" 2>/dev/null || true

  echo "Discord VPN split-tunnel routes removed."
}

status() {
  echo "=== Interface ==="
  if ip link show "$IFACE" &>/dev/null; then
    echo "$IFACE is UP"
    wg show "$IFACE" 2>/dev/null || true
  else
    echo "$IFACE is DOWN"
  fi

  echo ""
  echo "=== Policy Rules (fwmark $FWMARK) ==="
  ip rule show | grep -i "fwmark" || echo "(none)"

  echo ""
  echo "=== Routing Table $TABLE ==="
  ip route show table "$TABLE" 2>/dev/null || echo "(empty)"

  echo ""
  echo "=== Mangle OUTPUT rules ==="
  iptables -t mangle -L OUTPUT -n -v 2>/dev/null | grep -i "mark" || echo "(none)"

  echo ""
  echo "=== Mangle PREROUTING rules ==="
  iptables -t mangle -L PREROUTING -n -v 2>/dev/null | grep -i "mark" || echo "(none)"

  echo ""
  echo "=== NAT POSTROUTING ==="
  iptables -t nat -L POSTROUTING -n -v 2>/dev/null | grep "$IFACE" || echo "(none)"

  echo ""
  echo "=== Route check ==="
  echo -n "Discord (162.159.128.233): "
  ip route get 162.159.128.233 2>/dev/null | head -1
  echo -n "Normal  (8.8.8.8):         "
  ip route get 8.8.8.8 2>/dev/null | head -1
}

case "${1:-}" in
  start)  start ;;
  stop)   stop ;;
  status) status ;;
  *)
    echo "Usage: $0 {start|stop|status}" >&2
    exit 1
    ;;
esac
