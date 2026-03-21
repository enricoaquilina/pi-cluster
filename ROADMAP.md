# Homelab Cluster Roadmap

## Completed

| Phase | Description | Date |
|-------|-------------|------|
| 3 | Recovery playbook, NFS fix, openclaw rename | 2026-03-19 |
| 4 | NiPoGi heavy compute node | 2026-03-20 |
| 5 | Intelligent task routing (32ms cached) | 2026-03-20 |
| 6 | Self-healing watchdog + Telegram alerts | 2026-03-21 |
| 7 | Multi-model orchestration (6 models) | 2026-03-21 |
| 8 | Production hardening + Mission Control integration | 2026-03-21 |

## Upcoming

### Phase 9: Service Migration to Heavy
**Goal:** Move n8n + MongoDB from master to heavy, freeing master's RAM for agent workloads.

- [ ] 9.1 — Set up Docker on heavy (if not already)
- [ ] 9.2 — Deploy MongoDB on heavy, migrate data from master
- [ ] 9.3 — Deploy n8n on heavy, point to new MongoDB, verify workflows
- [ ] 9.4 — Update Cloudflare tunnel / reverse proxy for new n8n location
- [ ] 9.5 — Stop old n8n + MongoDB on master, verify nothing breaks
- [ ] 9.6 — Update Mission Control service checks for new locations
- [ ] 9.7 — Update smoke test + health checks

**Prerequisite:** Ethernet cable for heavy (reduces migration risk with reliable NFS)

### Phase 10: Register Master as Compute Node
**Goal:** Master becomes a 4th node host (8GB Pi 5), adding ~5GB usable RAM to the cluster.

- [ ] 10.1 — Install OpenClaw node host on master (v2026.3.11)
- [ ] 10.2 — Deploy exec-approvals + systemd service
- [ ] 10.3 — Pair with gateway (master connects to itself)
- [ ] 10.4 — Add to inventory as `openclaw_nodes` member (role: orchestrator)
- [ ] 10.5 — Update router: add master as fallback node for all task types
- [ ] 10.6 — Update stats collector, MC feed, pairing script
- [ ] 10.7 — Stress test: 4-node routing under load

**Prerequisite:** Phase 9 complete (services off master)

### Phase 11: Ethernet for Heavy
**Goal:** Replace WiFi/Tailscale with LAN for reliability and performance.

- [ ] 11.1 — Connect ethernet cable to heavy, get 192.168.0.x IP
- [ ] 11.2 — Update inventory, SSH configs, NFS exports (remove WiFi/Tailscale subnets)
- [ ] 11.3 — Update pairing script, router, stats collector with LAN IP
- [ ] 11.4 — Update Mission Control node hostname
- [ ] 11.5 — Test NFS performance (should be <1ms vs ~200ms)
- [ ] 11.6 — Remove Tailscale dependency for heavy (keep as backup)

**Prerequisite:** Physical ethernet cable

### Phase 12: Token Budget Tracking
**Goal:** Track OpenRouter spend per task type, alert on budget limits.

- [ ] 12.1 — Create budget config (daily/weekly limits per task type)
- [ ] 12.2 — Build token logger: intercept dispatch calls, log model + tokens used
- [ ] 12.3 — Build spend calculator using OpenRouter pricing
- [ ] 12.4 — Daily Telegram summary: spend by model, by task type
- [ ] 12.5 — Alert when approaching budget threshold (80%, 100%)
- [ ] 12.6 — Add spend data to Mission Control dashboard

### Phase 13: End-to-End Cluster Test Suite
**Goal:** Automated tests that verify the entire cluster is working.

- [ ] 13.1 — Test: dispatch to each node, verify output
- [ ] 13.2 — Test: interpreter commands (python3, node) on all nodes
- [ ] 13.3 — Test: NFS read/write from each node
- [ ] 13.4 — Test: router returns correct node for each task type
- [ ] 13.5 — Test: health check returns valid data for all nodes
- [ ] 13.6 — Test: MC API has fresh data (<5 min) for all nodes
- [ ] 13.7 — Run as daily cron + on-demand via `make openclaw-test`
- [ ] 13.8 — Telegram alert on test failure

## Future Ideas (Unscheduled)

- **Load-based task queuing** — if all nodes >80% RAM, queue tasks instead of overloading
- **Multi-workspace isolation** — different projects on different nodes
- **Agent performance dashboard** — success/fail rates, avg duration, model comparison
- **Backup automation** — nightly backup of gateway config, MC database, agent state
- **Auto-scaling** — spin up cloud instances when cluster is at capacity
