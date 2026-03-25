# Disaster Recovery Procedures

## Backup Locations

| Location | Path | Contents |
|----------|------|----------|
| Local (master) | `/mnt/external/backups/backup-YYYY-MM-DD.tar.gz` | Full daily backup |
| Off-site (heavy) | `heavy:/home/enrico/backups/backup-YYYY-MM-DD.tar.gz` | Rsync copy |

Retention: 14 days on both local and remote.

**Important notes (updated 2026-03-25):**
- Gateway config (openclaw.json, paired.json) is fetched from **heavy** via SSH (not local on master)
- Dispatch log is fetched from **heavy** `/tmp/openclaw-dispatch-log.db`
- MongoDB data is on heavy's **local storage** (`/home/enrico/mongodb-data/`), NOT on NFS
- Backup size should be >100K — if smaller, the backup script may have lost connectivity to heavy

## Backup Contents

Each backup tarball contains:

```
YYYY-MM-DD/
  gateway/
    openclaw.json          # Gateway configuration
    paired.json            # Device pairing (all 4 nodes)
    cloudflared.yml        # Cloudflare tunnel config
  identities/
    master-operator.json   # Master operator identity
    master-node.json       # Master node identity
    slave0.json            # Build node identity
    slave1.json            # Light node identity
    heavy.json             # Heavy node identity
  dispatch/
    openclaw-dispatch-log.db  # SQLite dispatch history
  mc/
    missioncontrol.sql     # PostgreSQL dump of Mission Control
  ansible/
    *.yml                  # Vault secrets, vars, inventory
```

## Restore Procedures

### Prerequisites

- SSH access to the master Pi (192.168.0.22)
- SSH access to all nodes (slave0, slave1, heavy)
- Docker and docker-compose installed

### 1. Restore from Local Backup

```bash
# Find latest backup
ls -lt /mnt/external/backups/backup-*.tar.gz | head -1

# Extract to temp directory
BACKUP="/mnt/external/backups/backup-2026-03-23.tar.gz"
TMP=$(mktemp -d)
tar -xzf "$BACKUP" -C "$TMP"
DATE=$(ls "$TMP")
```

### 2. Restore from Off-site (if Master Drive Failed)

```bash
# Copy backup from heavy node
scp 192.168.0.5:/home/enrico/backups/backup-2026-03-23.tar.gz /tmp/

# Extract
TMP=$(mktemp -d)
tar -xzf /tmp/backup-2026-03-23.tar.gz -C "$TMP"
DATE=$(ls "$TMP")
```

### 3. Restore Gateway Configuration

```bash
# Stop gateway
cd /mnt/external/openclaw && docker compose down

# Restore config
cp "$TMP/$DATE/gateway/openclaw.json" /home/enrico/.openclaw/openclaw.json

# Restore Cloudflare tunnel (if needed)
sudo cp "$TMP/$DATE/gateway/cloudflared.yml" /etc/cloudflared/config.yml
```

### 4. Restore Device Pairing (paired.json)

This is the most critical file -- it contains the cryptographic pairing between all nodes.

```bash
# Restore paired.json
sudo cp "$TMP/$DATE/gateway/paired.json" /home/enrico/.openclaw/devices/paired.json
sudo chmod 600 /home/enrico/.openclaw/devices/paired.json
sudo chown enrico:enrico /home/enrico/.openclaw/devices/paired.json
```

If paired.json is corrupted or missing, you must re-pair all nodes:

```bash
# Re-pair from scratch (requires gateway running)
cd /mnt/external/openclaw && docker compose up -d
make openclaw-pair
```

### 5. Restore Node Identities

```bash
# Master identities
cp "$TMP/$DATE/identities/master-operator.json" /home/enrico/.openclaw/identity/device.json
cp "$TMP/$DATE/identities/master-node.json" /home/enrico/.openclaw-node/.openclaw/identity/device.json

# Remote nodes
for node in slave0 slave1 heavy; do
    scp "$TMP/$DATE/identities/$node.json" "$node:/home/enrico/.openclaw/identity/device.json" 2>/dev/null || \
    scp "$TMP/$DATE/identities/$node.json" "$node:/home/enrico/.openclaw-node/.openclaw/identity/device.json"
done
```

### 6. Restore Mission Control Database

```bash
# Drop and recreate the MC database
docker exec -i mission-control-db psql -U missioncontrol -c "DROP DATABASE IF EXISTS missioncontrol_restore;"
docker exec -i mission-control-db psql -U missioncontrol -c "CREATE DATABASE missioncontrol_restore;"
docker exec -i mission-control-db psql -U missioncontrol missioncontrol_restore < "$TMP/$DATE/mc/missioncontrol.sql"

# Swap databases (downtime: ~5 seconds)
docker exec mission-control-db psql -U missioncontrol -c "
  SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='missioncontrol' AND pid <> pg_backend_pid();
"
docker exec mission-control-db psql -U missioncontrol -c "ALTER DATABASE missioncontrol RENAME TO missioncontrol_old;"
docker exec mission-control-db psql -U missioncontrol -c "ALTER DATABASE missioncontrol_restore RENAME TO missioncontrol;"
```

### 7. Restore Dispatch Log

```bash
# Stop cluster service
sudo systemctl stop openclaw-cluster-service

# Restore dispatch log
cp "$TMP/$DATE/dispatch/openclaw-dispatch-log.db" /tmp/openclaw-dispatch-log.db

# Restart
sudo systemctl start openclaw-cluster-service
```

### 8. Restart All Services

```bash
# Restart gateway
cd /mnt/external/openclaw && docker compose up -d

# Restart cluster service
sudo systemctl restart openclaw-cluster-service

# Verify all nodes reconnect
sleep 10
make openclaw-test
```

## Validation

After any restore, run the DR test to verify everything:

```bash
make dr-test
```

And the full E2E suite:

```bash
make openclaw-test
```

## Automated DR Testing

The DR validation script runs weekly (Sunday 4am) via cron and sends a Telegram alert on failure. To run manually:

```bash
bash scripts/openclaw-dr-test.sh
```
