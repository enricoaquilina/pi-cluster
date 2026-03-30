"""Background tasks: heartbeat sweep, budget snapshots, node snapshots."""

import asyncio
import logging

from .config import HEARTBEAT_STALE_SECONDS, BUDGET_DAILY, BUDGET_WEEKLY, BUDGET_MONTHLY
from .db import _pool
from .event_bus import event_bus
from .budget_helpers import _fetch_openrouter_usage

logger = logging.getLogger("mission-control")


async def _heartbeat_sweep():
    """Periodically mark nodes as offline when heartbeat is stale."""
    while True:
        await asyncio.sleep(60)
        conn = _pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE nodes
                       SET status = 'offline', updated_at = now()
                       WHERE status != 'offline'
                         AND last_heartbeat < now() - interval '%s seconds'""",
                    (HEARTBEAT_STALE_SECONDS,),
                )
                if cur.rowcount > 0:
                    logger.info("Heartbeat sweep: marked %d node(s) offline", cur.rowcount)
                    event_bus.publish("nodes")
            conn.commit()
        except Exception as e:
            logger.warning("Heartbeat sweep error: %s", e)
        finally:
            _pool.putconn(conn)


async def _budget_snapshot():
    """Store budget snapshot hourly. Takes initial snapshot 10s after startup."""
    await asyncio.sleep(10)
    while True:
        conn = _pool.getconn()
        try:
            usage = _fetch_openrouter_usage()
            if "error" not in usage:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO budget_snapshots
                           (daily_usd, weekly_usd, monthly_usd, total_usd,
                            daily_limit, weekly_limit, monthly_limit)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (usage["daily_usd"], usage["weekly_usd"],
                         usage["monthly_usd"], usage["total_usd"],
                         BUDGET_DAILY, BUDGET_WEEKLY, BUDGET_MONTHLY),
                    )
                    cur.execute(
                        "DELETE FROM budget_snapshots WHERE snapshot_at < now() - interval '90 days'"
                    )
                conn.commit()
                logger.info("Budget snapshot stored: $%.4f daily", usage["daily_usd"])
        except Exception as e:
            logger.warning("Budget snapshot error: %s", e)
        finally:
            _pool.putconn(conn)
        await asyncio.sleep(3600)


async def _node_snapshot():
    """Store node metrics snapshot hourly. First snapshot 15s after startup."""
    await asyncio.sleep(15)
    while True:
        conn = _pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT name, ram_used_mb, ram_total_mb, cpu_percent, metadata FROM nodes")
                rows = cur.fetchall()
                data = [(name, ram_used, ram_total, cpu,
                         (meta or {}).get("disk_pct", 0), (meta or {}).get("temp_c", 0))
                        for name, ram_used, ram_total, cpu, meta in rows]
                cur.executemany(
                    """INSERT INTO node_snapshots
                       (node_name, ram_used_mb, ram_total_mb, cpu_percent, disk_pct, temp_c)
                       VALUES (%s, %s, %s, %s, %s, %s)""", data)
                cur.execute(
                    "DELETE FROM node_snapshots WHERE snapshot_at < now() - interval '90 days'"
                )
            conn.commit()
        except Exception as e:
            logger.warning("Node snapshot error: %s", e)
        finally:
            _pool.putconn(conn)
        await asyncio.sleep(3600)
