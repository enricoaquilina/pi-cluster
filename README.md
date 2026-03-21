# Homelab — Distributed AI Agent Cluster

Ansible-managed infrastructure for a 4-node cluster running distributed OpenClaw agent workloads with intelligent task routing and multi-model orchestration.

## Architecture

```
                    ┌─────────────────────────┐
                    │     master (Pi 5 8GB)    │
                    │  Gateway · Router API    │
                    │  n8n · Docker · MongoDB  │
                    └─────┬──────┬──────┬──────┘
                          │      │      │
              ┌───────────┘      │      └───────────┐
              │                  │                   │
     ┌────────▼────────┐ ┌──────▼──────┐  ┌────────▼────────┐
     │ build (Pi 5 4G) │ │light (Pi4 2G│  │ heavy (16GB x86)│
     │ role: coding    │ │role: research│  │ role: compute   │
     │ Sonnet 4.6      │ │Qwen 3.5 Plus│  │ Opus 4.6        │
     └─────────────────┘ └─────────────┘  └─────────────────┘
```

## Nodes

| Node | Hardware | IP | Role | OpenClaw |
|------|----------|----|------|----------|
| **master** | Pi 5 8GB | 192.168.0.22 | Gateway, control plane | v2026.3.11 |
| **build** (slave0) | Pi 5 4GB | 192.168.0.3 | Coding node host | v2026.3.11 |
| **light** (slave1) | Pi 4 2GB | 192.168.0.4 | Research node host | v2026.3.11 |
| **heavy** | NiPoGi P2, AMD Ryzen 3, 16GB | Tailscale | Compute node host | v2026.3.11 |

## Intelligent Task Routing

Tasks are automatically routed to the best-fit node based on role affinity and real-time health (RAM, CPU load). The router uses cached stats (refreshed every 30s) for sub-40ms decisions.

| Task Type | Routes To | AI Model | Fallbacks |
|-----------|----------|----------|-----------|
| `coding` | build | Claude Sonnet 4.6 | GLM-5, MiniMax M2.7 |
| `research` | light | Qwen 3.5 Plus | MiniMax M2.7, Gemini Flash Lite |
| `compute` | heavy | Claude Opus 4.6 | GPT-5.4, GLM-5 |
| `any` | least loaded | MiniMax M2.7 | Gemini Flash, DeepSeek V3.2 |

If a preferred node is overloaded (>85% RAM), falls back to the next best available.

## Self-Healing

- **Cluster watchdog** (2min timer): detects disconnected nodes, auto-re-pairs
- **Telegram alerts**: notification on auto-recovery or unrecoverable failures
- **NFS watchdog** (2min timer): auto-remounts dropped NFS shares
- **Nightly version check** (2am): safely tests new OpenClaw versions, rolls back if broken

## Services

| Service | Host | Port | Description |
|---------|------|------|-------------|
| OpenClaw Gateway | master | 18789 | Agent gateway (WebSocket) |
| Router API | master | 8520 | HTTP API for task routing |
| Pi-hole VIP | 192.168.0.53 | 53 | HA DNS (keepalived failover) |
| n8n | heavy | 5678 | Workflow automation (migrated from master) |

## Make Targets

### Cluster Operations
| Command | Description |
|---------|-------------|
| `make openclaw-recovery` | Full disaster recovery (nodes + NFS + monitoring + pairing) |
| `make openclaw-pair` | Re-pair all nodes with gateway |
| `make openclaw-health` | Cluster health check |
| `make openclaw-dispatch coding "cmd"` | Route and execute command on best node |
| `make openclaw-route coding` | Show which node would handle a task type |
| `make openclaw-version` | Check for newer OpenClaw version |
| `make openclaw-upgrade` | Test and apply upgrade if safe |

### Infrastructure
| Command | Description |
|---------|-------------|
| `make ping` | Test connectivity to all nodes |
| `make update` | apt dist-upgrade on all nodes |
| `make status` | Show uptime |
| `make doctor` | Full diagnostics (connectivity, DNS, VIP, disk, memory) |
| `make pihole-ha` | Deploy keepalived + Gravity Sync |
| `make vpn` | Deploy KeepSolid VPN config |

### CI/CD
| Command | Description |
|---------|-------------|
| `make lint` | YAML lint + Ansible lint + ShellCheck |
| `make test` | Template rendering + syntax check |
| `make validate` | Full lint + test |

## CI/CD Pipeline

PRs to `master` trigger:
1. **YAML Lint** + **Ansible Lint** + **ShellCheck** — code quality
2. **Ansible Syntax Check** + **Dry Run** — playbook validation
3. **Template Rendering** — Jinja2 template tests
4. **PR-Agent** (OpenRouter/Gemini Flash) — AI code review
5. **Claude Code Action** — auto-fixes review comments (`@claude`)

## DNS Architecture

```
Devices → Pi-hole VIP 192.168.0.53 (keepalived)
            ├── slave0 Pi-hole (MASTER, priority 150)
            └── slave1 Pi-hole (BACKUP, priority 100)
         → Fallback: Cloudflare 1.1.1.1
```

## Secrets

Encrypted with Ansible Vault:
- `secrets/openclaw.yml` — gateway token
- `secrets/monitoring.yml` — Telegram bot credentials
- `secrets/pihole.yml` — keepalived auth
- `secrets/vpn.yml` — KeepSolid VPN keys

## Project Structure

```
homelab/
├── playbooks/           # Ansible playbooks
├── inventory/           # Hosts and group vars
├── vars/                # Variable files
├── templates/           # Jinja2 templates (systemd, configs)
├── scripts/             # Bash scripts (router, watchdog, health, pairing)
├── skills/              # OpenClaw MCP skills (cluster-dispatch)
├── secrets/             # Ansible Vault encrypted secrets
├── tests/               # CI tests (template rendering, syntax)
├── openclaw/            # Custom Docker build + MCP proxy
├── .github/workflows/   # CI/CD pipelines
└── Makefile             # All cluster operations
```
