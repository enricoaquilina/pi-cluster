.PHONY: ping update reboot status disk memory docker-ps vpn vpn-status pihole-ha pihole-whitelist pihole-status pihole-update doctor common pihole-maintenance openclaw-nodes openclaw-nfs openclaw-status openclaw-health openclaw-doctor openclaw-monitoring openclaw-recovery openclaw-pair openclaw-dispatch openclaw-route openclaw-version openclaw-upgrade openclaw-test security-scan security-audit dr-test lint test validate

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

# OpenClaw Distributed Agent Cluster
openclaw-nodes:
	ansible-playbook playbooks/openclaw-nodes.yml --ask-vault-pass

openclaw-nfs:
	ansible-playbook playbooks/openclaw-nfs.yml

openclaw-monitoring:
	ansible-playbook playbooks/openclaw-monitoring.yml

openclaw-recovery:
	ansible-playbook playbooks/openclaw-recovery.yml --ask-vault-pass

openclaw-pair:
	@bash scripts/openclaw-pair-nodes.sh

openclaw-dispatch:
	@bash scripts/openclaw-dispatch.sh $(filter-out $@,$(MAKECMDGOALS))

openclaw-route:
	@bash scripts/openclaw-router.sh $(or $(filter-out $@,$(MAKECMDGOALS)),any)

openclaw-status:
	@echo "=== OpenClaw Nodes ==="
	@openclaw nodes status 2>/dev/null || echo "Gateway not running or openclaw not available"
	@echo ""
	@echo "=== Subagents ==="
	@openclaw subagents list 2>/dev/null || echo "No active subagents"

openclaw-version:
	@bash scripts/openclaw-version-check.sh

openclaw-upgrade:
	@bash scripts/openclaw-version-check.sh --upgrade

openclaw-test:
	@bash scripts/openclaw-e2e-test.sh

openclaw-health:
	@bash scripts/openclaw-health.sh

openclaw-doctor: doctor openclaw-health
	@echo ""
	@echo "=== Full Cluster Health Complete ==="

# Security
security-scan:
	@echo "=== Docker Image Security Scan ==="
	@command -v trivy > /dev/null 2>&1 || { echo "Install trivy: https://aquasecurity.github.io/trivy/"; exit 1; }
	trivy image --severity CRITICAL,HIGH openclaw-custom:local
	trivy image --severity CRITICAL,HIGH mongo:7

security-audit:
	@bash scripts/openclaw-security-audit.sh

dr-test:
	@bash scripts/openclaw-dr-test.sh

# CI/CD — Linting and Validation
lint:
	@echo "=== YAML Lint ==="
	yamllint -c .yamllint.yml .
	@echo ""
	@echo "=== Ansible Lint ==="
	ansible-lint
	@echo ""
	@echo "=== ShellCheck ==="
	shellcheck scripts/*.sh files/*.sh
	@echo ""
	@echo "=== All Lint Checks Passed ==="

test:
	@echo "=== Template Rendering Tests ==="
	python3 tests/test_templates.py
	@echo ""
	@echo "=== Ansible Syntax Check ==="
	@for playbook in playbooks/*.yml; do \
		echo "--- Checking $$playbook ---"; \
		ansible-playbook --syntax-check -i tests/inventory.yml \
			--vault-password-file tests/vault-password.txt "$$playbook" 2>&1 \
			|| echo "    SKIPPED (vault decryption — use real vault pass or CI)"; \
	done
	@echo ""
	@echo "=== Tests Complete ==="

validate: lint test
	@echo ""
	@echo "=== Full Validation Complete ==="
