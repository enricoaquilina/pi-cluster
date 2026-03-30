# Mission Control

FastAPI dashboard for the Pi cluster. Monitors nodes, services, dispatch, and the PARA knowledge base.

## Quick Start

```bash
cd /home/enrico/mission-control
docker compose up -d --build
```

Dashboard: http://heavy:3000 (Cloudflare Access: mc.siliconsentiments.work)

## Memory System

The Memories tab searches three sources:

| Source | Badge | Location | Description |
|--------|-------|----------|-------------|
| Workspace | Blue | `/openclaw/workspace/*.md` | OpenClaw project files |
| Life | Green | `~/life/` (PARA structure) | Knowledge graph, daily notes, tacit knowledge |
| Memory | Purple | `/openclaw/memory/main.sqlite` | FTS5 indexed chunks |

### API Endpoints

- `GET /api/memories?q=search&limit=50&offset=0` — search across all sources
- `GET /api/memories/file?path=life/Projects/pi-cluster/summary.md` — retrieve file content
- `GET /api/life/daily-status` — today's daily note status (exists, size, consolidated)

### PARA Structure (~/life/)

```
Projects/     → Active projects with outcomes
Areas/        → Ongoing responsibilities (about-me/ has identity docs)
Resources/    → Reference material + procedural skills
Archives/     → Completed/inactive items
People/       → Entity folders for important people
Companies/    → Entity folders for companies
Daily/        → YYYY/MM/YYYY-MM-DD.md session logs
```

Each entity folder contains `summary.md` (overview) and `items.json` (structured facts).

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LIFE_DIR` | `/life` | Mount point for ~/life/ PARA directory |
| `OPENCLAW_DIR` | `/openclaw` | Mount point for OpenClaw workspace |
| `DATABASE_URL` | — | PostgreSQL connection string |
| `API_KEY` | — | Authentication key for protected endpoints |

## Other Endpoints

- `GET /health` — service health check
- `GET /api/nodes` — cluster node status
- `GET /api/services` — service health
- `GET /api/dispatch` — task dispatch to Maxwell personas
- `GET /api/dispatch/personas` — persona routing table
- `GET /api/dispatch/log` — dispatch history
