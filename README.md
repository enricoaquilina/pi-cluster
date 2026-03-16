# Homelab - Raspberry Pi Cluster

Ansible-managed infrastructure for a 3-node Raspberry Pi cluster.

## Nodes

| Node | Hardware | IP | Role |
|------|----------|----|------|
| master | Pi 5 8GB | 192.168.0.x | Orchestration, n8n, Docker workloads |
| slave0 | Pi 5 4GB | 192.168.0.3 | Pi-hole (MASTER), keepalived |
| slave1 | Pi 4 2GB | 192.168.0.4 | Pi-hole (BACKUP), keepalived |

## DNS Architecture

All devices (via Tailscale MagicDNS)
  -> Pi-hole VIP: 192.168.0.53 (keepalived, floats between slave0/slave1)
      -> slave0 Pi-hole (priority 150, MASTER)
      -> slave1 Pi-hole (priority 100, BACKUP)
  -> Fallback: Cloudflare 1.1.1.1 / 1.0.0.1 (configured in Tailscale admin)

Tailscale DNS settings (https://login.tailscale.com/admin/dns):
- Global nameservers: 192.168.0.53 (Pi-hole VIP) + Cloudflare Public DNS
- Override DNS servers: ON
- MagicDNS: ON

## Playbooks

| Command | Description |
|---------|-------------|
| make ping | Test connectivity to all nodes |
| make update | apt dist-upgrade on all cluster nodes |
| make pihole-ha | Deploy keepalived + Gravity Sync + whitelists |
| make pihole-whitelist | Update Pi-hole whitelists only |
| make pihole-status | Check Pi-hole status on all nodes |
| make vpn | Deploy KeepSolid VPN config |
| make status | Show uptime for all nodes |

## Secrets

Encrypt with: ansible-vault encrypt secrets/pihole.yml secrets/vpn.yml

## Adding Work Domains to Whitelist

1. Add domain to pihole_whitelist in vars/pihole.yml
2. Run: make pihole-ha
3. Gravity Sync propagates between nodes automatically
