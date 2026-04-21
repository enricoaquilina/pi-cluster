# Smoke Checks

Modular health checks sourced by `system-smoke-test.sh`. Each file exports one or more `check_*()` functions that call `check_service NAME STATUS [MESSAGE]` from `lib/smoke-common.sh`.

## Modules

| # | File | What it checks |
|---|------|---------------|
| 01 | openclaw-gateway | Gateway health + container memory |
| 02 | openclaw-comms | Telegram alerting pipeline |
| 03 | databases | MongoDB, PostgreSQL connectivity |
| 04 | n8n | Production + staging instances |
| 05 | openclaw-nodes | Node connectivity via gateway API |
| 06 | polymarket-bot | Copybot service status |
| 07 | spreadbot | Spreadbot service + position tracking |
| 08 | dns | Pi-hole DNS resolution (primary + backup) |
| 09 | network | Tailscale, Cloudflare tunnel, internet |
| 10 | storage | NFS mounts, disk usage, backup freshness |
| 11 | hardware | CPU temp, memory pressure, USB devices |
| 12 | life-sync | ~/life git sync + QMD index freshness |

## Adding a check

1. Create `NN-name.sh` with a `check_name()` function
2. Call `check_service "name" "up|down|degraded" ["optional message"]`
3. The runner sources all `smoke-checks/*.sh` files in numeric order
