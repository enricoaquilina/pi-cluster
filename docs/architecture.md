# OpenClaw Cluster Architecture

Last updated: 2026-04-21

## Cluster Overview

4-node distributed AI agent cluster (OpenClaw), managed via Ansible. Nodes are connected over a local LAN (192.168.0.0/24) with Tailscale overlay for remote nodes. All configuration lives in `/home/enrico/pi-cluster/` (aliased as `homelab` on master).

## Node Roles

| Node | Hardware | IPs | Role | Max Concurrent |
|------|----------|-----|------|----------------|
| **master** | Pi 5 8GB | 192.168.0.22 | NFS server, Pi-hole DNS, Cloudflare tunnel, monitoring (VM+Grafana), compute (orchestrator) | 5 |
| **build** (slave0) | Pi 5 4GB | 192.168.0.3, Tailscale 100.65.188.85 | Coding node, Pi-hole MASTER | 5 |
| **light** (slave1) | Pi 4 2GB | 192.168.0.4 | Research node, Pi-hole BACKUP | 3 |
| **heavy** | NiPoGi P2, AMD Ryzen 3, 12GB | 192.168.0.5, Tailscale 100.85.234.128 | ALL Docker services (except monitoring), compute | 10 |

### master (192.168.0.22)

- **Docker containers:** VictoriaMetrics (metrics DB), Grafana (dashboards) -- monitoring survives heavy outages
- NFS server (backup copy, hourly sync from heavy via `nfs-backup.sh`)
- Pi-hole DNS (upstream of VIP at 192.168.0.53)
- Cloudflare tunnel routing external traffic to heavy (n8n, MC)
- OpenClaw node agent (orchestrator role)
- Cron jobs: backups, health checks, heavy-watchdog, version checks, budget reports
- Ansible control node (runs playbooks against all nodes)

### build / slave0 (192.168.0.3)

- OpenClaw coding node (Claude Sonnet 4.6, fallbacks: GLM-5, MiniMax M2.7)
- Pi-hole MASTER (keepalived priority 150)
- Gravity Sync to slave1

### light / slave1 (192.168.0.4)

- OpenClaw research node (Qwen 3.5 Plus, fallbacks: MiniMax M2.7, Gemini Flash Lite)
- Pi-hole BACKUP (keepalived priority 100)
- Gravity Sync from slave0

### heavy (192.168.0.5)

- All Docker services (see below) -- monitoring moved to master
- OpenClaw compute node (Claude Opus 4.6, fallbacks: GPT-5.4, GLM-5)
- Claude Code sessions run from heavy

## Service Layout on Heavy

### Docker Compose (`/mnt/external/openclaw/`)

| Container | Port | Description |
|-----------|------|-------------|
| **gateway** | 18789 | OpenClaw gateway (WebSocket) |
| **MC API** | 127.0.0.1:8000 | Mission Control API |
| **MC proxy** | 3000 | Mission Control dashboard |
| **MC DB** (PostgreSQL) | 5432 | Mission Control database |
| **MongoDB** | 27017 | Agent data store |
| **n8n** | 5678 | Workflow automation |

MongoDB storage: `/home/enrico/mongodb-data/` (local, NOT NFS -- see Key Design Decisions).

### Systemd Services

| Unit | Type | Description |
|------|------|-------------|
| `openclaw-router-api` | service | HTTP router API on port 8520 |
| `openclaw-stats-collector` | timer (30s) | Collects node stats, updates cache + MC feed |
| `openclaw-watchdog-cluster` | timer (2min) | Detects disconnected nodes, auto-re-pairs |
| `openclaw-node` | service | OpenClaw node agent |
| `polymarket-bot` | service | Polymarket copy-trading bot (auto-restart, 250MB memory limit) |
| `system-smoke-test` | timer (5min) | Modular health checks: 12 check modules in `smoke-checks/` |
| `nfs-backup` | timer (1h) | Dumps MongoDB/MC/n8n + rsync to master via `scripts/nfs-backup.sh` |

## NFS Topology

Master exports two shares to all nodes:

```
/home/enrico/homelab  →  /opt/workspace      (project files)
/mnt/external         →  /mnt/external        (external storage)
```

Mount options use `all_squash,root_squash,anonuid=1000` to map all remote access to uid 1000 (enrico). This means **databases cannot run on NFS** -- WiredTiger (MongoDB) and other engines that use `flock`/`fcntl` locking or require uid-accurate file ownership will get `EPERM`.

## Intelligent Task Routing

The router API (port 8520) makes sub-40ms routing decisions using cached node stats (refreshed every 30s by stats-collector).

| Task Type | Preferred Node | AI Model | Fallback Nodes |
|-----------|---------------|----------|----------------|
| `coding` | build | Claude Sonnet 4.6 | GLM-5, MiniMax M2.7 |
| `research` | light | Qwen 3.5 Plus | MiniMax M2.7, Gemini Flash Lite |
| `compute` | heavy | Claude Opus 4.6 | GPT-5.4, GLM-5 |
| `any` | least loaded | MiniMax M2.7 | Gemini Flash, DeepSeek V3.2 |

If the preferred node is overloaded (>85% RAM), the router falls back to the next available node.

## Monitoring Pipeline

```
Node agents (all 4 nodes)
    │  push stats every 30s
    ▼
stats-collector (heavy, 30s timer)
    │  updates JSON cache + MC feed
    ▼
Router API (heavy:8520)        MC API (heavy:8000)
    │                              │
    ▼                              ▼
Task routing decisions         MC Dashboard (heavy:3000)
```

Resource monitor runs locally on each node (cron every 5 min), alerts via Telegram on state changes only (no spam).

## DNS Architecture

```
Devices  →  Pi-hole VIP 192.168.0.53 (keepalived)
              ├── slave0 Pi-hole (MASTER, priority 150)
              └── slave1 Pi-hole (BACKUP, priority 100)
           →  Fallback: Cloudflare 1.1.1.1
```

Gravity Sync keeps both Pi-hole instances synchronized.

## CI/CD Pipeline

```
PR opened
  → Lint (YAML, Ansible, ShellCheck)
  → Syntax Check + Dry Run
  → Template Rendering Tests
  → AI Review (PR-Agent + Gemini Flash)
  → Claude Fix (applies suggestions from @claude comments)
  → Auto-Merge
  → Auto-Deploy (5min cron on master pulls + runs Ansible)
```

## Backup Strategy

- **Hourly NFS sync** (heavy→master): `scripts/nfs-backup.sh` via systemd timer
  - Pre-sync dumps: MongoDB (`mongodump`), MC PostgreSQL (`pg_dump`), n8n workflows+credentials
  - Master copy at `/mnt/external/` — max 1h stale during emergency failover
- **Daily full backup** (3am cron on master): `scripts/openclaw-backup.sh`
  - Contents: gateway config, paired.json, node identities, MC dump, dispatch log, n8n workflows, Ansible secrets, polymarket-bot
  - Local retention: `/mnt/external/backups/` (14 days)
  - Off-site copy: rsync to heavy `/home/enrico/backups/` (14 days)
  - Cloud copy: Backblaze B2 via rclone (if configured)
- **DR validation**: weekly (Sunday 4am) via `scripts/openclaw-dr-test.sh`
- **Emergency failover**: `heavy-watchdog.sh` triggers after 6 min downtime → `emergency-restore-master.sh` restores from hourly dumps

## Memory & Knowledge System

Three-layer PARA knowledge base at `~/life/` on heavy, integrated with Mission Control.

### Layers

| Layer | Purpose | Storage |
|-------|---------|---------|
| Knowledge Graph | Durable facts about projects, people, companies | `~/life/{Projects,People,Companies,Resources}/*/summary.md + items.json` |
| Daily Notes | Session log, decisions, active project heartbeat | `~/life/Daily/YYYY/MM/YYYY-MM-DD.md` |
| Tacit Knowledge | Identity, rules, habits, lessons learned | `~/life/Areas/about-me/*.md` |

### Entity Rules
- Entity folder created when mentioned 3+ times, has direct relationship, or is significant
- Each entity has `summary.md` (overview) + `items.json` (structured facts with dates)
- Slug convention: lowercase, hyphens only (e.g. `pi-cluster`, `archie`)

### Nightly Consolidation (2 AM cron)
`~/life/scripts/nightly-consolidate.sh` calls `claude -p` (Haiku 4.5, pinned model) to:
1. Extract entities, facts, skills, and tacit knowledge from today's daily note
2. Apply changes via `apply_extraction.py` (dedup, conflict detection, JSON validation)
3. Touch heartbeat file; alert via Telegram if no daily note exists

### Mission Control Integration
- `GET /api/memories` — searches across workspace files, ~/life/ PARA files, and FTS5 index
- `GET /api/life/daily-status` — checks today's daily note existence and consolidation status
- Life files appear with green badges in the Memories tab
- Volume mount: `/home/enrico/life:/life:ro` in docker-compose

### Session Protocol
`/home/enrico/CLAUDE.md` auto-loads every Claude Code session, instructing the bot to:
- Read hard-rules.md and workflow-habits.md at start
- Create daily note if missing; write to it throughout the session
- Use `[[wiki-links]]` when referencing entities (Obsidian-compatible)

## Key Design Decisions

1. **MongoDB on local storage** -- NFS `all_squash` maps all file owners to anonuid=1000. WiredTiger requires accurate file ownership and locking, resulting in `EPERM` on NFS. Solution: MongoDB data lives at `/home/enrico/mongodb-data/` on heavy's local disk.

2. **Gateway CLI uses `node dist/index.js`** -- The `openclaw` binary inside the gateway container is broken. All gateway CLI operations must use `docker exec <container> node dist/index.js` instead.

3. **Python on control node needs `bash -c` wrapper** -- The newer agent version (v2026.3.23-1) on master blocks direct interpreter commands. Wrap Python/Node calls in `bash -c "python3 ..."` to bypass.

4. **Service IPs centralized in `.env.cluster`** -- All inter-service IP addresses are defined as environment variables in `scripts/.env.cluster`, never hardcoded in scripts. This file has mode 600.

5. **All Docker off master** -- Phase 9 migrated every Docker container to heavy, freeing master's 8GB RAM for compute workloads (Phase 10).

## Secrets

Encrypted with Ansible Vault in `secrets/`:

| File | Contents |
|------|----------|
| `openclaw.yml` | Gateway token |
| `monitoring.yml` | Telegram bot credentials |
| `pihole.yml` | Keepalived auth password |
| `vpn.yml` | KeepSolid VPN keys |
