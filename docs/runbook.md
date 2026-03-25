# OpenClaw Operations Runbook

Last updated: 2026-03-25

---

## Common Operations

### Deploy after PR merge

```bash
make deploy                  # Manual: pulls repo on master, updates NFS scripts
# Or wait for auto-deploy (5min cron on master)
```

### Run E2E tests

```bash
make openclaw-test                              # Quick test suite
bash scripts/openclaw-e2e-test.sh --full        # Full suite (includes gateway restart recovery)
make openclaw-test-alert                        # Run + send results to Telegram
```

### View logs

```bash
make logs                    # Aggregated logs across services (runs scripts/openclaw-logs.sh)
```

### Check cluster health

```bash
make openclaw-health         # Quick health check
make openclaw-doctor         # Full diagnostics (connectivity, DNS, VIP, disk, memory + health)
make doctor                  # Infrastructure-only diagnostics
```

### Run backup manually

```bash
ssh master "bash ~/homelab/scripts/openclaw-backup.sh"
```

### DR validation

```bash
make dr-test                 # Runs scripts/openclaw-dr-test.sh
```

### Check OpenClaw version

```bash
make openclaw-version        # Show current vs latest
make openclaw-upgrade        # Test new version + apply if safe
```

### Dispatch a task

```bash
make openclaw-dispatch coding "implement feature X"
make openclaw-route coding   # Show which node would handle it (dry run)
```

### Re-pair nodes

```bash
make openclaw-pair           # Re-pair all 4 nodes with gateway
```

---

## Troubleshooting

### Stale Mission Control heartbeats

MC dashboard shows old timestamps for one or more nodes.

1. Check the stats-collector timer on heavy:
   ```bash
   ssh heavy "systemctl status openclaw-stats-collector.timer"
   ssh heavy "systemctl status openclaw-stats-collector.service"
   ```
2. Check for MC feed errors:
   ```bash
   ssh heavy "journalctl -u openclaw-stats-collector --since '10 min ago' --no-pager"
   ```
3. Verify MC API is responding:
   ```bash
   ssh heavy "curl -sf http://127.0.0.1:8000/api/health"
   ```
4. Restart if needed:
   ```bash
   ssh heavy "sudo systemctl restart openclaw-stats-collector.timer"
   ```

### Failed backups (suspiciously small size)

Backup tarball is under 100K or missing expected contents.

1. Check if heavy is reachable from master:
   ```bash
   ssh master "ping -c1 192.168.0.5"
   ```
2. Verify `.env.cluster` has correct HEAVY_HOST:
   ```bash
   ssh master "grep HEAVY /home/enrico/homelab/scripts/.env.cluster"
   ```
3. Run backup manually and watch output:
   ```bash
   ssh master "bash -x ~/homelab/scripts/openclaw-backup.sh"
   ```
4. Check that Docker is running on heavy (needed for MC database dump):
   ```bash
   ssh heavy "docker ps --format '{{.Names}}'"
   ```

### Gateway nodes show disconnected

One or more nodes appear disconnected in gateway status.

1. Wait 2 minutes -- the cluster-watchdog auto-detects and re-pairs disconnected nodes.
2. If still disconnected, restart the node agent on the affected node:
   ```bash
   ssh <node> "sudo systemctl restart openclaw-node"
   ```
3. Check node agent logs:
   ```bash
   ssh <node> "journalctl -u openclaw-node --since '5 min ago' --no-pager"
   ```
4. If the gateway itself is the problem, see "Gateway down" in Emergency Procedures.

### NFS mount issues

Node cannot access `/opt/workspace` or `/mnt/external`.

1. Verify master is up:
   ```bash
   ping -c1 192.168.0.22
   ```
2. Check NFS exports on master:
   ```bash
   ssh master "exportfs -v"
   ```
3. Remount on the affected node:
   ```bash
   ssh <node> "sudo mount -a"
   ```
4. If mount hangs, the NFS server may be down. Check master's NFS service:
   ```bash
   ssh master "systemctl status nfs-kernel-server"
   ```

### Telegram alert spam

Repeated alerts from resource monitor or smoke tests.

1. Identify the source -- resource monitor alerts on state *changes* only, so repeated alerts suggest an oscillating condition (e.g., RAM flapping around 80%).
2. Check system-smoke-test.sh on master:
   ```bash
   ssh master "journalctl -u system-smoke-test --since '1 hour ago' --no-pager"
   ```
3. Verify service URLs in `.env.cluster` match the actual service locations:
   ```bash
   ssh master "cat ~/homelab/scripts/.env.cluster | grep -E 'URL|HOST|PORT'"
   ```
4. If a service moved (e.g., during migration), update `.env.cluster` and redeploy monitoring:
   ```bash
   make openclaw-monitoring
   ```

### Router returns wrong node

Task routes to unexpected node.

1. Check cached stats freshness:
   ```bash
   ssh heavy "curl -sf http://127.0.0.1:8520/stats"
   ```
2. Verify node roles in inventory:
   ```bash
   grep -A3 openclaw_node_role inventory/hosts.yml
   ```
3. Check if preferred node is overloaded (>85% RAM triggers fallback):
   ```bash
   ssh <node> "free -h"
   ```

---

## Emergency Procedures

### Gateway down

All nodes lose connectivity to the gateway.

```bash
# Restart gateway container on heavy
ssh heavy "cd /mnt/external/openclaw && docker compose restart openclaw-gateway"

# Verify gateway health
ssh heavy "curl -sf http://127.0.0.1:18789/healthz"

# If container won't start, check logs
ssh heavy "cd /mnt/external/openclaw && docker compose logs --tail=50 openclaw-gateway"
```

### Heavy node down

Heavy hosts all Docker services and monitoring.

**Automatic recovery:** The heavy-watchdog cron on master checks every 2 minutes. After 3 consecutive failures (6 minutes), it auto-runs `scripts/emergency-restore-master.sh` to restore critical services on master.

**Manual recovery:**
```bash
# From master (or any node with SSH to master)
ssh master "bash ~/homelab/scripts/emergency-restore-master.sh"
```

**When heavy comes back:**
```bash
# Verify heavy is reachable
ping -c3 192.168.0.5

# Restart services on heavy
ssh heavy "cd /mnt/external/openclaw && docker compose up -d"
ssh heavy "sudo systemctl restart openclaw-router-api openclaw-stats-collector.timer openclaw-watchdog-cluster.timer openclaw-node"

# Stop emergency services on master (if they were started)
ssh master "cd /mnt/external/openclaw && docker compose down"
```

### Master down

Nodes lose NFS mounts but continue running with cached data. Gateway/Docker are unaffected (they are on heavy).

1. Restore master from backup or fix hardware issue.
2. Once master is back:
   ```bash
   # Verify NFS exports
   ssh master "sudo systemctl restart nfs-kernel-server"
   ssh master "exportfs -v"

   # Remount NFS on all nodes
   ansible all -m shell -a "mount -a" --become

   # Run full health check
   make openclaw-doctor
   ```
3. If master storage is lost, restore from off-site backup on heavy:
   ```bash
   scp 192.168.0.5:/home/enrico/backups/backup-YYYY-MM-DD.tar.gz /tmp/
   ```
   See `docs/DISASTER-RECOVERY.md` for full restore procedure.

### Rotate secrets

```bash
# 1. Update secrets in .env.cluster
vim scripts/.env.cluster
chmod 600 scripts/.env.cluster

# 2. Update Ansible vault secrets if applicable
ansible-vault edit secrets/monitoring.yml
ansible-vault edit secrets/openclaw.yml

# 3. Redeploy monitoring (pushes .env.cluster to heavy)
make openclaw-monitoring

# 4. Restart affected services on heavy
ssh heavy "sudo systemctl restart openclaw-router-api openclaw-stats-collector.timer"

# 5. If gateway token changed, restart gateway + re-pair
ssh heavy "cd /mnt/external/openclaw && docker compose restart openclaw-gateway"
make openclaw-pair
```

---

## Monitoring Alerts

All alerts are sent to Telegram via the bot configured in `secrets/monitoring.yml`.

| Alert Source | Trigger | Frequency | Details |
|-------------|---------|-----------|---------|
| **Resource monitor** | RAM >80%, swap >50%, temp >75C, disk >85% | Every 5 min (alerts on state change only) | Runs on each node via cron |
| **E2E test** | Any test failure | Daily at 6am + on-demand | 13 tests covering SSH, dispatch, NFS, routing, MC, backups |
| **Heavy-watchdog** | Heavy unreachable for 3 checks (6 min) | Every 2 min on master | Auto-restores services to master after threshold |
| **Cluster-watchdog** | Disconnected nodes detected | Every 2 min on heavy | Auto-re-pairs disconnected nodes |
| **DR validation** | Backup missing, stale, or incomplete | Weekly (Sunday 4am) | Validates backup freshness, size, restore capability |
| **Version check** | New OpenClaw version available | Nightly at 2am | Tests before applying, rolls back if broken |

### Alert triage priority

1. **Heavy-watchdog "AUTO-RESTORING"** -- Heavy is down. Services are being moved to master. Investigate heavy node immediately.
2. **E2E test failures** -- Cluster functionality degraded. Run `make openclaw-test` to identify which tests fail.
3. **Cluster-watchdog "disconnected"** -- Node lost connection. Usually self-heals. Check if persistent.
4. **Resource monitor** -- Capacity warning. Check what is consuming resources on the affected node.
5. **DR validation** -- Backup issue. Not urgent but fix before next disaster.

---

## Useful Paths

| Path | Node | Description |
|------|------|-------------|
| `/home/enrico/pi-cluster/` | heavy (working copy) | Ansible repo |
| `/home/enrico/homelab/` | master (NFS) | Ansible repo (git-pulled by auto-deploy) |
| `/mnt/external/openclaw/` | heavy | Docker Compose for all services |
| `/home/enrico/.openclaw/` | each node | OpenClaw operator config + identity |
| `/home/enrico/.openclaw-node/` | each node | OpenClaw node agent config |
| `/home/enrico/mongodb-data/` | heavy (local) | MongoDB data (NOT on NFS) |
| `/mnt/external/backups/` | master | Daily backups (14-day retention) |
| `/home/enrico/backups/` | heavy | Off-site backup copies (14-day retention) |
| `scripts/.env.cluster` | heavy | Centralized service IPs and secrets (mode 600) |
