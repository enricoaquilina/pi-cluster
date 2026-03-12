# openclaw-custom

Custom Docker image layer for OpenClaw gateway on Raspberry Pi 5.

Extends the base `openclaw:local` image with Python packages required by workspace skills.

## Included packages

| Package | Required by | Purpose |
|---------|------------|---------|
| `tavily-python` | tavily skill | AI-optimized web search |
| `requests` | n8n skill | n8n workflow API calls |

## Usage

Build the custom image:

```bash
docker build -t openclaw-custom:local .
```

Or via docker-compose (from `/mnt/external/openclaw`):

```bash
docker compose build openclaw-gateway
docker compose up -d openclaw-gateway
```

## Prerequisites

The base `openclaw:local` image must exist (built from the main OpenClaw repo).

## Adding new skill dependencies

1. Add the pip package to the `RUN pip install` line in the Dockerfile
2. Rebuild: `docker compose build openclaw-gateway && docker compose up -d openclaw-gateway`
