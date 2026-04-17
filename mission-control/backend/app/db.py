"""Database connection pool and initialization."""

import psycopg2
import psycopg2.extras
import psycopg2.pool

from .config import DATABASE_URL

psycopg2.extras.register_uuid()

_pool = psycopg2.pool.ThreadedConnectionPool(
    minconn=1, maxconn=10, dsn=DATABASE_URL
)


def get_db():
    conn = _pool.getconn()
    try:
        yield conn
    finally:
        _pool.putconn(conn)


def init_db():
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    title       TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    status      TEXT NOT NULL DEFAULT 'todo',
                    priority    TEXT NOT NULL DEFAULT 'medium',
                    assignee    TEXT NOT NULL DEFAULT 'enrico',
                    project     TEXT NOT NULL DEFAULT '',
                    tags        TEXT[] NOT NULL DEFAULT '{}',
                    due_date    TIMESTAMPTZ,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
                CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee);
                CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project);

                CREATE TABLE IF NOT EXISTS dispatch_log (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    persona TEXT NOT NULL,
                    node TEXT NOT NULL,
                    delegate TEXT NOT NULL,
                    fallback BOOLEAN NOT NULL DEFAULT FALSE,
                    original_node TEXT,
                    prompt_preview TEXT NOT NULL DEFAULT '',
                    response_preview TEXT NOT NULL DEFAULT '',
                    elapsed_ms INT NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'success',
                    error_detail TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                -- Add columns if upgrading from older schema
                DO $$ BEGIN
                    ALTER TABLE dispatch_log ADD COLUMN original_node TEXT;
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$;
                DO $$ BEGIN
                    ALTER TABLE dispatch_log ADD COLUMN model TEXT;
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$;
                CREATE INDEX IF NOT EXISTS idx_dispatch_log_created ON dispatch_log(created_at DESC);

                CREATE TABLE IF NOT EXISTS nodes (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    name TEXT UNIQUE NOT NULL,
                    hostname TEXT NOT NULL,
                    hardware TEXT NOT NULL DEFAULT '',
                    framework TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'offline',
                    ram_total_mb INT NOT NULL DEFAULT 0,
                    ram_used_mb INT NOT NULL DEFAULT 0,
                    cpu_percent FLOAT NOT NULL DEFAULT 0,
                    last_heartbeat TIMESTAMPTZ,
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );

                CREATE TABLE IF NOT EXISTS service_checks (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    service TEXT NOT NULL,
                    status TEXT NOT NULL,
                    response_ms INT,
                    error TEXT,
                    checked_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                CREATE INDEX IF NOT EXISTS idx_service_checks_lookup
                    ON service_checks (service, checked_at DESC);

                CREATE TABLE IF NOT EXISTS service_alerts (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    service TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT,
                    downtime_seconds INT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                CREATE INDEX IF NOT EXISTS idx_service_alerts_lookup
                    ON service_alerts (created_at DESC);

                CREATE TABLE IF NOT EXISTS budget_snapshots (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    daily_usd DOUBLE PRECISION NOT NULL,
                    weekly_usd DOUBLE PRECISION NOT NULL,
                    monthly_usd DOUBLE PRECISION NOT NULL,
                    total_usd DOUBLE PRECISION NOT NULL,
                    daily_limit DOUBLE PRECISION NOT NULL,
                    weekly_limit DOUBLE PRECISION NOT NULL,
                    monthly_limit DOUBLE PRECISION NOT NULL,
                    snapshot_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                CREATE INDEX IF NOT EXISTS idx_budget_snapshots_at
                    ON budget_snapshots (snapshot_at DESC);

                CREATE TABLE IF NOT EXISTS provider_balances (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    provider TEXT NOT NULL,
                    balance_usd DOUBLE PRECISION,
                    used_credits INT,
                    total_credits INT,
                    raw_json JSONB DEFAULT '{}',
                    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                CREATE INDEX IF NOT EXISTS idx_provider_balances_lookup
                    ON provider_balances (provider, fetched_at DESC);

                CREATE TABLE IF NOT EXISTS node_snapshots (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    node_name TEXT NOT NULL,
                    ram_used_mb INT,
                    ram_total_mb INT,
                    cpu_percent FLOAT,
                    disk_pct INT,
                    temp_c INT,
                    snapshot_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                CREATE INDEX IF NOT EXISTS idx_node_snapshots_lookup
                    ON node_snapshots (node_name, snapshot_at DESC);

                -- NVMe health columns (added for SSD write protection)
                DO $$ BEGIN
                    ALTER TABLE node_snapshots ADD COLUMN nvme_wear_pct INT;
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$;
                DO $$ BEGIN
                    ALTER TABLE node_snapshots ADD COLUMN nvme_written_gb INT;
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$;
                DO $$ BEGIN
                    ALTER TABLE node_snapshots ADD COLUMN disk_write_mb_s FLOAT;
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$;
            """)
        conn.commit()
    finally:
        _pool.putconn(conn)
