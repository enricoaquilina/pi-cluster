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

## Dispatch System

13 personas with per-persona model routing and vault-grounded system prompts.

### Endpoints

- `POST /api/dispatch` — dispatch a prompt to a persona
- `GET /api/dispatch/personas` — persona routing table (model, delegate, team)
- `GET /api/dispatch/log` — dispatch history with filters

### Personas & Models

| Category | Personas | Model |
|----------|----------|-------|
| Orchestrator | Maxwell | openai/gpt-5.5 |
| Security | Sentinel | openai/gpt-5.5 |
| Engineering | Archie, Harbor, Ledger | deepseek/deepseek-v4 |
| Structured | Pixel, Docsworth, Quill | qwen/qwen-3-27b |
| Strategy/Design | Stratton, Flux, Sigil | zhipu-ai/glm-5.1 |
| Research | Scout, Chroma | moonshot/kimi-k2.6 |

Each persona gets a vault-grounded system prompt with per-persona segment selection (grounding, identity, rules, daily note, etc.).

## PRD Lifecycle

DB-backed planning documents for the heartbeat-runner → Telegram approval flow.

### Endpoints

- `POST /api/prd` — create/upsert PRD (re-create resets to pending)
- `GET /api/prd/{slug}` — get PRD by slug
- `GET /api/prd?status=pending` — list PRDs with optional status filter
- `PATCH /api/prd/{slug}` — update PRD fields (title, content, telegram_message_id)
- `POST /api/prd/{slug}/approve` — approve pending PRD
- `POST /api/prd/{slug}/reject` — reject with optional feedback

### Status Flow

```
pending → approved (next heartbeat dispatches with PRD context)
        → rejected (with feedback → heartbeat regenerates)
```

## Other Endpoints

- `GET /health` — service health check
- `GET /api/nodes` — cluster node status
- `GET /api/services` — service health
