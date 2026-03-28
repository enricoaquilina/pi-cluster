"""Stats endpoint."""

from fastapi import APIRouter, Depends

from ..db import get_db

router = APIRouter()


@router.get("/api/stats")
def stats(conn=Depends(get_db)):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status = 'todo') AS todo,
                COUNT(*) FILTER (WHERE status = 'in_progress') AS in_progress,
                COUNT(*) FILTER (WHERE status = 'blocked') AS blocked,
                COUNT(*) FILTER (WHERE status = 'review') AS review,
                COUNT(*) FILTER (WHERE status = 'done') AS done,
                COUNT(*) FILTER (WHERE assignee = 'enrico') AS enrico,
                COUNT(*) FILTER (WHERE assignee = 'maxwell') AS maxwell
            FROM tasks
        """)
        row = cur.fetchone()
    cols = ["total", "todo", "in_progress", "blocked", "review", "done", "enrico", "maxwell"]
    return dict(zip(cols, row))
