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
| 12 | Token budget tracking + daily Telegram digest | 2026-03-21 |
| 13 | E2E cluster test suite (36 tests, daily cron) | 2026-03-21, expanded 2026-03-25 |
| 14 | Centralized logging: logrotate, journald, Docker limits | 2026-03-25 |
| 15 | CI/CD pipeline: AI review → Claude fix → auto-merge → auto-deploy | 2026-03-25 |
| 16 | Security hardening: token extraction, permissions, audit | 2026-03-25 |
| 17 | Monitoring: resource alerts, IP centralization, silent failure hardening | 2026-03-25 |
| 18 | Auto-deploy, failover tests, documentation overhaul | 2026-03-25 |

## Future Ideas (Prioritized)

### Near-term
- **Cloud backup** — Add B2/S3 as 3rd backup location (currently master + heavy only)
- **Mac SSH direct** — SSH from Mac to heavy via LAN IP
- **API rate limiting** — Prevent abuse of cluster service endpoints

### Medium-term
- **E2E failover expansion** — NFS mount loss recovery, concurrent dispatch under load
- **Agent performance dashboard** — success/fail rates, avg duration, model comparison
- **Structured logging** (JSON) — replace text logs for better parsing
- **Pre-deploy validation** — CI validates inventory matches actual network

### Long-term
- **Secrets management** — Vault/Sealed Secrets instead of .env.cluster
- **Multi-workspace isolation** — different projects on different nodes
- **Auto-scaling** — spin up cloud instances when cluster is at capacity
