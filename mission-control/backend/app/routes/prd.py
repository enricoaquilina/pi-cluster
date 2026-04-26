"""PRD lifecycle endpoints — create, read, approve, reject."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import verify_api_key
from ..db import get_db
from ..event_bus import event_bus
from ..models.prd import PrdCreate, PrdAction

logger = logging.getLogger("mission-control")

router = APIRouter()


def _row_to_response(row, cols) -> dict:
    d = dict(zip(cols, row))
    d["created_at"] = d["created_at"].isoformat() if d["created_at"] else None
    d["updated_at"] = d["updated_at"].isoformat() if d["updated_at"] else None
    return d


_PRD_COLS = [
    "slug", "title", "task_id", "content", "status", "feedback",
    "model", "telegram_message_id", "created_at", "updated_at",
]
_PRD_SELECT = ", ".join(_PRD_COLS)


@router.post("/api/prd", status_code=201)
def create_prd(body: PrdCreate, conn=Depends(get_db), _=Depends(verify_api_key)):
    """Upsert a PRD. On conflict (slug), replace content and reset status to pending."""
    with conn.cursor() as cur:
        cur.execute(
            f"""INSERT INTO prd (slug, title, task_id, content, model)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (slug) DO UPDATE SET
                    title = EXCLUDED.title,
                    task_id = EXCLUDED.task_id,
                    content = EXCLUDED.content,
                    model = EXCLUDED.model,
                    status = 'pending',
                    feedback = NULL,
                    updated_at = now()
                RETURNING {_PRD_SELECT}""",
            (body.slug, body.title, body.task_id, body.content, body.model),
        )
        row = cur.fetchone()
    conn.commit()
    event_bus.publish("prd")
    return _row_to_response(row, _PRD_COLS)


@router.get("/api/prd/{slug}")
def get_prd(slug: str, conn=Depends(get_db), _=Depends(verify_api_key)):
    """Get a single PRD by slug."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_PRD_SELECT} FROM prd WHERE slug = %s", (slug,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"PRD '{slug}' not found")
    return _row_to_response(row, _PRD_COLS)


@router.get("/api/prd")
def list_prds(
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn=Depends(get_db),
    _=Depends(verify_api_key),
):
    """List PRDs with optional status filter."""
    clauses = []
    params: list = []
    if status:
        clauses.append("status = %s")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM prd {where}", params)
        total = cur.fetchone()[0]
        cur.execute(
            f"""SELECT {_PRD_SELECT} FROM prd {where}
                ORDER BY updated_at DESC LIMIT %s OFFSET %s""",
            params + [limit, offset],
        )
        rows = [_row_to_response(r, _PRD_COLS) for r in cur.fetchall()]
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.post("/api/prd/{slug}/approve")
def approve_prd(slug: str, body: PrdAction = None, conn=Depends(get_db), _=Depends(verify_api_key)):
    """Set PRD status to approved."""
    with conn.cursor() as cur:
        cur.execute(
            f"""UPDATE prd SET status = 'approved', updated_at = now()
                WHERE slug = %s AND status = 'pending'
                RETURNING {_PRD_SELECT}""",
            (slug,),
        )
        row = cur.fetchone()
    if not row:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM prd WHERE slug = %s", (slug,))
            existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail=f"PRD '{slug}' not found")
        raise HTTPException(
            status_code=409,
            detail=f"PRD '{slug}' is '{existing[0]}', not 'pending'",
        )
    conn.commit()
    event_bus.publish("prd")
    logger.info("prd: approved slug=%s", slug)
    return _row_to_response(row, _PRD_COLS)


@router.post("/api/prd/{slug}/reject")
def reject_prd(slug: str, body: PrdAction = None, conn=Depends(get_db), _=Depends(verify_api_key)):
    """Set PRD status to rejected with optional feedback."""
    feedback = body.feedback if body else None
    with conn.cursor() as cur:
        cur.execute(
            f"""UPDATE prd SET status = 'rejected', feedback = %s, updated_at = now()
                WHERE slug = %s AND status = 'pending'
                RETURNING {_PRD_SELECT}""",
            (feedback, slug),
        )
        row = cur.fetchone()
    if not row:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM prd WHERE slug = %s", (slug,))
            existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail=f"PRD '{slug}' not found")
        raise HTTPException(
            status_code=409,
            detail=f"PRD '{slug}' is '{existing[0]}', not 'pending'",
        )
    conn.commit()
    event_bus.publish("prd")
    logger.info("prd: rejected slug=%s feedback=%s", slug, feedback[:100] if feedback else None)
    return _row_to_response(row, _PRD_COLS)
