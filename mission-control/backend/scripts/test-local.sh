#!/bin/bash
# Run MC backend tests against a temporary PostgreSQL container.
# Usage: bash scripts/test-local.sh [pytest args...]
set -euo pipefail

cd "$(dirname "$0")/.."

CONTAINER="mc-test-db-$$"
PORT=5433

cleanup() { docker rm -f "$CONTAINER" 2>/dev/null || true; }
trap cleanup EXIT

echo "[test] Starting PostgreSQL container ($CONTAINER)..."
docker run -d --name "$CONTAINER" -p "$PORT":5432 \
  -e POSTGRES_USER=missioncontrol \
  -e POSTGRES_PASSWORD=testpassword \
  -e POSTGRES_DB=missioncontrol \
  postgres:16-alpine >/dev/null

echo "[test] Waiting for PostgreSQL..."
for _ in $(seq 1 15); do
  python3 -c "import psycopg2; psycopg2.connect('postgresql://missioncontrol:testpassword@localhost:$PORT/missioncontrol')" 2>/dev/null && break
  sleep 1
done

echo "[test] Running pytest..."
DATABASE_URL="postgresql://missioncontrol:testpassword@localhost:$PORT/missioncontrol" \
  python3 -m pytest tests/ -v "$@"
