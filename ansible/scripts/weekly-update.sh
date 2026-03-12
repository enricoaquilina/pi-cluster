#!/bin/bash
cd ~/homelab
git pull --ff-only
ansible-playbook playbooks/update.yml >> ~/homelab/logs/update.log 2>&1

