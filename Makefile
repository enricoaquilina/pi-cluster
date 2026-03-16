.PHONY: ping update reboot status disk memory logs docker-ps vpn vpn-status pihole-ha pihole-whitelist pihole-status

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
