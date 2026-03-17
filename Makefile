.PHONY: ping update reboot status disk memory docker-ps vpn vpn-status pihole-ha pihole-whitelist pihole-status pihole-update doctor common pihole-maintenance

ping:
	ansible all -m ping

update:
	ansible-playbook playbooks/update.yml

reboot:
	ansible all -m reboot --become

status:
	ansible all -a "uptime"

disk:
	ansible all -a "df -h /"

memory:
	ansible all -a "free -h"

docker-ps:
	ansible all -a "docker ps --format 'table {{.Names}}\t{{.Status}}'" --become

vpn:
	ansible-playbook playbooks/vpn.yml --ask-vault-pass

vpn-status:
	ansible vpn -a "/usr/local/bin/discord-vpn-routes.sh status" --become

pihole-ha:
	ansible-playbook playbooks/pihole-ha.yml --ask-vault-pass

pihole-whitelist:
	ansible-playbook playbooks/pihole-ha.yml --ask-vault-pass

pihole-status:
	ansible pihole -a "pihole status" --become

pihole-update:
	ansible pihole -a "pihole -up" --become

common:
	ansible-playbook playbooks/common.yml

doctor:
	@echo "=== Connectivity ==="
	@ansible all -m ping
	@echo ""
	@echo "=== Uptime ==="
	@ansible all -a "uptime"
	@echo ""
	@echo "=== Pi-hole Status ==="
	@ansible pihole -a "pihole status" --become
	@echo ""
	@echo "=== Keepalived VIP ==="
	@ansible pihole -m shell -a "ip addr show eth0 | grep 192.168.0.53 && echo 'VIP: ACTIVE' || echo 'VIP: not on this node'" --become
	@echo ""
	@echo "=== DNS Resolution via VIP ==="
	@ansible pihole -m shell -a "dig @192.168.0.53 +short +time=3 google.com && echo 'DNS: OK' || echo 'DNS: FAILED'" --become
	@echo ""
	@echo "=== DNS Resolution per node ==="
	@ansible pihole -m shell -a "dig @127.0.0.1 +short +time=3 google.com && echo 'Local DNS: OK' || echo 'Local DNS: FAILED'" --become
	@echo ""
	@echo "=== Gravity Sync Timers ==="
	@ansible pihole -m shell -a "systemctl list-timers gravity-sync.timer --no-pager" --become
	@echo ""
	@echo "=== Disk Usage ==="
	@ansible all -a "df -h /"
	@echo ""
	@echo "=== Memory ==="
	@ansible all -a "free -h"

pihole-maintenance:
	ansible-playbook playbooks/pihole-maintenance.yml
