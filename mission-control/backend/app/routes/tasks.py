"""Task CRUD endpoints."""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import verify_api_key, rate_limit
from ..db import get_db
from ..event_bus import event_bus
from ..helpers import row_to_dict
from ..models.tasks import TaskCreate, TaskUpdate, TaskResponse, TASK_COLUMNS

router = APIRouter()


@router.get("/api/tasks", response_model=list[TaskResponse])
def list_tasks(
    status: Optional[str] = Query(None),
    assignee: Optional[str] = Query(None),
    project: Optional[str] = Query(None),
    conn=Depends(get_db),
):
    clauses = []
    params = []
    if status:
        clauses.append("status = %s")
        params.append(status)
    if assignee:
        clauses.append("assignee = %s")
        params.append(assignee)
    if project:
        clauses.append("project = %s")
        params.append(project)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = f"SELECT {', '.join(TASK_COLUMNS)} FROM tasks {where} ORDER BY created_at DESC"

    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    return [row_to_dict(r, TASK_COLUMNS) for r in rows]


@router.get("/api/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: uuid.UUID, conn=Depends(get_db)):
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(TASK_COLUMNS)} FROM tasks WHERE id = %s",
            (task_id,),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    return row_to_dict(row, TASK_COLUMNS)


@router.post("/api/tasks", response_model=TaskResponse, status_code=201)
def create_task(task: TaskCreate, conn=Depends(get_db), _=Depends(verify_api_key), __=Depends(rate_limit)):
    with conn.cursor() as cur:
        cur.execute(
            f"""INSERT INTO tasks (title, description, status, priority, assignee, project, tags, due_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING {', '.join(TASK_COLUMNS)}""",
            (
                task.title, task.description, task.status.value,
                task.priority.value, task.assignee, task.project,
                task.tags, task.due_date,
            ),
        )
        row = cur.fetchone()
    conn.commit()
    event_bus.publish("tasks")
    return row_to_dict(row, TASK_COLUMNS)


@router.patch("/api/tasks/{task_id}", response_model=TaskResponse)
def update_task(
    task_id: uuid.UUID, task: TaskUpdate, conn=Depends(get_db), _=Depends(verify_api_key), __=Depends(rate_limit)
):
    updates = {}
    data = task.model_dump(exclude_unset=True)
    for key, val in data.items():
        if val is not None:
            updates[key] = val.value if isinstance(val, Enum) else val

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates["updated_at"] = datetime.now(timezone.utc)
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [task_id]

    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE tasks SET {set_clause} WHERE id = %s RETURNING {', '.join(TASK_COLUMNS)}",
            values,
        )
        row = cur.fetchone()
    conn.commit()
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    event_bus.publish("tasks")
    return row_to_dict(row, TASK_COLUMNS)


@router.delete("/api/tasks/{task_id}", status_code=204)
def delete_task(task_id: uuid.UUID, conn=Depends(get_db), _=Depends(verify_api_key), __=Depends(rate_limit)):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Task not found")
    conn.commit()
    event_bus.publish("tasks")
