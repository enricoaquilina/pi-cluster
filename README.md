# Homelab — Distributed AI Agent Cluster

Ansible-managed infrastructure for a 4-node cluster running distributed OpenClaw agent workloads with intelligent task routing and multi-model orchestration.

## Architecture

```
     ┌─────────────────────────┐
     │     master (Pi 5 8GB)   │
     │  NFS server · Pi-hole   │
     │  Cloudflare tunnel      │
     │  Backups · Orchestrator │
     └─────┬──────┬──────┬─────┘
           │      │      │
 ┌─────────┘      │      └─────────┐
 │                │                 │
 ▼                ▼                 ▼
┌──────────┐ ┌──────────┐ ┌────────────────────┐
│  build   │ │  light   │ │       heavy        │
│ Pi 5 4GB │ │ Pi 4 2GB │ │ NiPoGi P2 12GB    │
│ coding   │ │ research │ │ compute + services │
│          │ │          │ │ Gateway · MC · n8n │
│          │ │          │ │ MongoDB · Router   │
└──────────┘ └──────────┘ └────────────────────┘
```

## Nodes

| Node | Hardware | IP | Role |
|------|----------|----|------|
| **master** | Pi 5 8GB | 192.168.0.22 | NFS server, DNS, backups, orchestrator node |
| **build** (slave0) | Pi 5 4GB | 192.168.0.3 / Tailscale | Coding node, Pi-hole MASTER |
| **light** (slave1) | Pi 4 2GB | 192.168.0.4 | Research node, Pi-hole BACKUP |
| **heavy** | NiPoGi P2 AMD Ryzen 3 12GB | 192.168.0.5 / Tailscale | All Docker services, monitoring, compute |

## Services (all on heavy)

| Service | Port | Description |
|---------|------|-------------|
| OpenClaw Gateway | 18789 | Agent gateway (WebSocket) |
| Router API | 8520 | Task routing + dispatch + budget |
| Mission Control API | 127.0.0.1:8000 | Dashboard backend (PostgreSQL) |
| Mission Control UI | 3000 | Dashboard frontend (Caddy) |
| MongoDB | 127.0.0.1:27017 | Data store (local storage) |
| n8n | 5678 | Workflow automation |
| Pi-hole VIP | 192.168.0.53 | HA DNS (keepalived failover) |

## Task Routing

Tasks are routed to the best-fit node based on role affinity and real-time health (RAM, CPU). Stats refreshed every 30s.

| Task Type | Primary | Fallbacks |
|-----------|---------|-----------|
| `coding` | build | heavy, light |
| `research` | light | heavy, build |
| `compute` | heavy | build, light |
| `orchestrator` | master (control) | heavy |

## Self-Healing

- **Cluster watchdog** (2min): detects disconnected nodes, auto-re-pairs
- **Heavy watchdog** (2min on master): monitors heavy, auto-restores to master after 6min failure
- **NFS watchdog** (2min): auto-remounts dropped shares
- **Resource monitor** (5min): RAM/swap/temp/disk alerts via Telegram
- **E2E test** (daily 6am): 36 tests, Telegram alert on failure

## Operations

| Command | Description |
|---------|-------------|
| `make deploy` | Sync NFS scripts from git (also runs automatically every 5min) |
| `make openclaw-test` | Run E2E test suite (36 tests) |
| `make logs` | View cluster-wide logs |
| `make openclaw-health` | Health check all nodes |
| `make dr-test` | Disaster recovery validation |
| `make validate` | Lint + test + permission check |
| `make log-maintenance` | Deploy logrotate, journald limits, cleanup crons |
| `make openclaw-monitoring` | Deploy all monitoring (crons, watchdogs, resource monitor) |

## CI/CD Pipeline

```
PR opened → Auto-merge armed (gh pr merge --auto)
         → AI Review (MiniMax 2.7 via OpenRouter/PR-Agent)
         → Lint (ShellCheck, ruff, yamllint, gitleaks) + Validate + Security Scan
         → If CI fails → Claude Sonnet 4.6 auto-fixes + pushes (via GitHub App token)
         → All 6 required checks pass → GitHub native auto-merge → MERGED
         → Auto-Deploy (5min cron on master)
```

**Branch protection on `master`:** requires ShellCheck, YAML Lint, Ansible Syntax Check, Python Lint, Script Smoke Test, AI Code Review.

**GitHub App:** `pi-cluster-bot` generates tokens for Claude fix commits that re-trigger CI workflows.

## Monitoring & Alerting

Alerts via Telegram:
- Resource thresholds: RAM >80%, swap >50%, temp >75°C, disk >85%
- E2E test failures (daily)
- Node disconnections (auto-recovery attempted)
- Heavy node unreachable (auto-restore after 6min)
- Service restarts (mc-watchdog on heavy)
- Structured JSON logging via `scripts/lib/log.sh` (bash) and `_JsonFormatter` (Python)

### Memory & Knowledge Base

PARA-structured knowledge system at `~/life/` on heavy with automation in `life-automation/`:
- **Knowledge Graph** — entity folders with `summary.md` + `items.json` (confidence scoring, temporal decay)
- **Daily Notes** — session logs at `Daily/YYYY/MM/YYYY-MM-DD.md`, heartbeat reference
- **Tacit Knowledge** — rules, habits, lessons in `Areas/about-me/`

Nightly consolidation (2 AM) extracts facts from daily notes via Claude Haiku, with entity slug normalization, temporal-aware decay, and summary size monitoring. The `~/life/scripts/` directory symlinks to `life-automation/` in this repo. See [architecture.md](docs/architecture.md) for details.

## Project Structure

```
homelab/
├── playbooks/           # Ansible playbooks
├── inventory/           # Hosts and group vars
├── vars/                # Variable files
├── templates/           # Jinja2 templates (systemd, configs)
├── scripts/             # Bash/Python scripts (router, watchdog, health, etc.)
├── life-automation/     # Knowledge base automation (symlinked from ~/life/scripts/)
│   └── tests/           # pytest suite (281 tests)
├── secrets/             # Ansible Vault encrypted secrets
├── docs/                # Architecture, runbook, DR procedures
├── .github/workflows/   # CI/CD: review, fix, merge, lint, security
└── Makefile             # All cluster operations
```
