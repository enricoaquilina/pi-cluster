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
| 9 | Service migration to heavy (all Docker off master) | 2026-03-24 |
| 10 | Master registered as 4th compute node (orchestrator) | 2026-03-24 |
| 11 | Ethernet for heavy, WiFi disabled (dormant fallback) | 2026-03-24 |

## Upcoming

### Phase 10.5: Mac SSH to Heavy
**Goal:** SSH directly from Mac to heavy for Claude Code sessions.

- [ ] 10.5.1 — Add heavy to Mac's `~/.ssh/config` (direct LAN IP 192.168.0.5)
- [ ] 10.5.2 — Copy Mac SSH key to heavy (`ssh-copy-id enrico@heavy`)
- [ ] 10.5.3 — Verify `ssh heavy` from Mac works
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
