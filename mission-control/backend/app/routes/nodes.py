"""Node CRUD and metrics endpoints."""

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import verify_api_key, rate_limit
from ..db import get_db
from ..event_bus import event_bus
from ..helpers import row_to_dict
from ..models.nodes import NodeCreate, NodeUpdate, NodeResponse, NODE_COLUMNS

router = APIRouter()


@router.get("/api/nodes", response_model=list[NodeResponse])
def list_nodes(conn=Depends(get_db)):
    with conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(NODE_COLUMNS)} FROM nodes ORDER BY name")
        rows = cur.fetchall()
    return [row_to_dict(r, NODE_COLUMNS) for r in rows]


@router.post("/api/nodes", response_model=NodeResponse, status_code=201)
def upsert_node(node: NodeCreate, conn=Depends(get_db), _=Depends(verify_api_key), __=Depends(rate_limit)):
    """Upsert a node -- create if new, update if exists."""
    with conn.cursor() as cur:
        cur.execute(
            f"""INSERT INTO nodes (name, hostname, hardware, framework, status,
                    ram_total_mb, ram_used_mb, cpu_percent, last_heartbeat, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (name) DO UPDATE SET
                    hostname = EXCLUDED.hostname,
                    hardware = EXCLUDED.hardware,
                    framework = EXCLUDED.framework,
                    status = EXCLUDED.status,
                    ram_total_mb = EXCLUDED.ram_total_mb,
                    ram_used_mb = EXCLUDED.ram_used_mb,
                    cpu_percent = EXCLUDED.cpu_percent,
                    last_heartbeat = EXCLUDED.last_heartbeat,
                    metadata = EXCLUDED.metadata,
                    updated_at = now()
                RETURNING {', '.join(NODE_COLUMNS)}""",
            (
                node.name, node.hostname, node.hardware, node.framework,
                node.status, node.ram_total_mb, node.ram_used_mb,
                node.cpu_percent, node.last_heartbeat,
                json.dumps(node.metadata),
            ),
        )
        row = cur.fetchone()
    conn.commit()
    event_bus.publish("nodes")
    return row_to_dict(row, NODE_COLUMNS)


@router.patch("/api/nodes/{name}", response_model=NodeResponse)
def update_node(name: str, node: NodeUpdate, conn=Depends(get_db), _=Depends(verify_api_key), __=Depends(rate_limit)):
    updates = {}
    data = node.model_dump(exclude_unset=True)
    for key, val in data.items():
        if val is not None:
            updates[key] = json.dumps(val) if key == "metadata" else val

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates["updated_at"] = datetime.now(timezone.utc)
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [name]

    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE nodes SET {set_clause} WHERE name = %s RETURNING {', '.join(NODE_COLUMNS)}",
            values,
        )
        row = cur.fetchone()
    conn.commit()
    if not row:
        raise HTTPException(status_code=404, detail="Node not found")
    event_bus.publish("nodes")
    return row_to_dict(row, NODE_COLUMNS)


@router.get("/api/nodes/{name}/metrics")
def node_metrics(name: str, days: int = Query(7, ge=1, le=90), conn=Depends(get_db)):
    """Historical node metrics for trend analysis."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT node_name, ram_used_mb, ram_total_mb, cpu_percent,
                   disk_pct, temp_c, snapshot_at
            FROM node_snapshots
            WHERE node_name = %s AND snapshot_at > now() - interval '1 day' * %s
            ORDER BY snapshot_at ASC
            LIMIT 500
        """, (name, days))
        cols = ["node_name", "ram_used_mb", "ram_total_mb", "cpu_percent",
                "disk_pct", "temp_c", "snapshot_at"]
        rows = cur.fetchall()
    return [{**dict(zip(cols, r)), "snapshot_at": r[6].isoformat()} for r in rows]
