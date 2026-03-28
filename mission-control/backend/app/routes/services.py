"""Service monitoring endpoints."""

import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import verify_api_key, rate_limit
from ..db import get_db
from ..event_bus import event_bus
from ..models.services import ServiceCheckBulk, ServiceAlertIn

router = APIRouter()

_last_smoke_trigger = 0.0


@router.post("/api/services/check", status_code=201)
def bulk_insert_checks(payload: ServiceCheckBulk, conn=Depends(get_db), _=Depends(verify_api_key), __=Depends(rate_limit)):
    with conn.cursor() as cur:
        for c in payload.checks:
            cur.execute(
                """INSERT INTO service_checks (service, status, response_ms, error, checked_at)
                   VALUES (%s, %s, %s, %s, COALESCE(%s, now()))""",
                (c.service, c.status, c.response_ms, c.error, c.checked_at),
            )
    conn.commit()
    event_bus.publish("services")
    return {"inserted": len(payload.checks)}


@router.get("/api/services")
def list_services(conn=Depends(get_db)):
    with conn.cursor() as cur:
        # Get latest check per service
        cur.execute("""
            SELECT DISTINCT ON (service) service, status, response_ms, checked_at
            FROM service_checks
            ORDER BY service, checked_at DESC
        """)
        latest = {r[0]: {"service": r[0], "current_status": r[1], "last_response_ms": r[2], "last_checked": r[3].isoformat() if r[3] else None} for r in cur.fetchall()}

        # Uptime calculations — single query instead of N*2
        cur.execute("""
            SELECT service,
                   COUNT(*) FILTER (WHERE status IN ('up', 'degraded') AND checked_at > now() - interval '24 hours') AS ok_24h,
                   COUNT(*) FILTER (WHERE checked_at > now() - interval '24 hours') AS total_24h,
                   COUNT(*) FILTER (WHERE status IN ('up', 'degraded') AND checked_at > now() - interval '168 hours') AS ok_7d,
                   COUNT(*) FILTER (WHERE checked_at > now() - interval '168 hours') AS total_7d
            FROM service_checks
            WHERE checked_at > now() - interval '168 hours'
            GROUP BY service
        """)
        for svc, ok_24, total_24, ok_7d, total_7d in cur.fetchall():
            if svc in latest:
                latest[svc]["uptime_24h"] = round(ok_24 / total_24 * 100, 1) if total_24 > 0 else None
                latest[svc]["uptime_7d"] = round(ok_7d / total_7d * 100, 1) if total_7d > 0 else None

        # Last incident per service — single query instead of N
        cur.execute("""
            SELECT DISTINCT ON (service) service, created_at
            FROM service_alerts
            ORDER BY service, created_at DESC
        """)
        incidents = {r[0]: r[1].isoformat() for r in cur.fetchall()}
        for svc in latest:
            latest[svc].setdefault("uptime_24h", None)
            latest[svc].setdefault("uptime_7d", None)
            latest[svc]["last_incident"] = incidents.get(svc)

    return list(latest.values())


@router.get("/api/services/alerts")
def list_service_alerts(hours: int = Query(24, ge=1, le=168), conn=Depends(get_db)):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, service, status, message, downtime_seconds, created_at
            FROM service_alerts
            WHERE created_at > now() - interval '1 hour' * %s
            ORDER BY created_at DESC
        """, (hours,))
        cols = ["id", "service", "status", "message", "downtime_seconds", "created_at"]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            if r["created_at"]:
                r["created_at"] = r["created_at"].isoformat()
            r["id"] = str(r["id"])
    return rows


@router.post("/api/services/alert", status_code=201)
def record_alert(alert: ServiceAlertIn, conn=Depends(get_db), _=Depends(verify_api_key), __=Depends(rate_limit)):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO service_alerts (service, status, message, downtime_seconds)
               VALUES (%s, %s, %s, %s)
               RETURNING id, created_at""",
            (alert.service, alert.status, alert.message, alert.downtime_seconds),
        )
        row = cur.fetchone()
    conn.commit()
    event_bus.publish("services")
    return {"id": str(row[0]), "created_at": row[1].isoformat()}


@router.post("/api/services/check/trigger")
async def trigger_smoke_test(_=Depends(verify_api_key), __=Depends(rate_limit)):
    global _last_smoke_trigger
    now = time.monotonic()
    if now - _last_smoke_trigger < 60:
        raise HTTPException(status_code=429, detail="Rate limited — max 1 trigger per 60 seconds")
    _last_smoke_trigger = now
    try:
        proc = await asyncio.create_subprocess_exec(
            "/usr/local/bin/system-smoke-test.sh", "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        try:
            result = json.loads(stdout.decode())
        except Exception:
            result = {"raw": stdout.decode(), "stderr": stderr.decode()}
        event_bus.publish("services")
        return result
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Smoke test timed out after 30s")
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="Smoke test script not found")


@router.get("/api/services/{name}/history")
def service_history(name: str, hours: int = Query(24, ge=1, le=168), conn=Depends(get_db)):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT service, status, response_ms, error, checked_at
            FROM service_checks
            WHERE service = %s AND checked_at > now() - interval '1 hour' * %s
            ORDER BY checked_at ASC
        """, (name, hours))
        cols = ["service", "status", "response_ms", "error", "checked_at"]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            if r["checked_at"]:
                r["checked_at"] = r["checked_at"].isoformat()
    return rows
