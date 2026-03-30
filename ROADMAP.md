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
| 19 | WhatsApp hardening, agent diagnostics, nightly auto-update | 2026-03-26 |
| 20 | Security: API key rotation, git history scrub, repo made public | 2026-03-26 |
| 21 | CI/CD overhaul: MiniMax 2.7 review, Claude auto-fix, native auto-merge | 2026-03-26 |
| 22 | Code quality: connection pooling, enum enforcement, JSON logging, pre-commit | 2026-03-26 |
| 23 | Memory & Knowledge: PARA system at ~/life/, nightly consolidation, MC integration | 2026-03-30 |

## Future Ideas (Prioritized)

### Near-term
- **Cloud backup** — Add B2/S3 as 3rd backup location (currently master + heavy only)
- **Monitoring stack** — VictoriaMetrics + Grafana for time-series metrics
- **Mac SSH direct** — SSH from Mac to heavy via LAN IP

### Medium-term
- **E2E failover expansion** — NFS mount loss recovery, concurrent dispatch under load
- **Agent performance dashboard** — success/fail rates, avg duration, model comparison
- **Voice/STT** — n8n middleware + Whisper API for WhatsApp/Telegram voice notes
- **Pre-deploy validation** — CI validates inventory matches actual network

### Long-term
- **QMD (Quantified Me Dashboard)** — Personal metrics dashboard fed by ~/life/ knowledge graph
- **Secrets management** — Vault/Sealed Secrets instead of .env.cluster
- **Multi-workspace isolation** — different projects on different nodes
- **Auto-scaling** — spin up cloud instances when cluster is at capacity
