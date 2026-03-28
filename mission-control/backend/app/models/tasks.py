"""Task models and constants."""

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    todo = "todo"
    in_progress = "in_progress"
    blocked = "blocked"
    review = "review"
    done = "done"


class TaskPriority(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    urgent = "urgent"


class TaskCreate(BaseModel):
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.todo
    priority: TaskPriority = TaskPriority.medium
    assignee: str = "enrico"
    project: str = ""
    tags: list[str] = Field(default_factory=list)
    due_date: Optional[datetime] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[TaskStatus] = None
    priority: Optional[TaskPriority] = None
    assignee: Optional[str] = None
    project: Optional[str] = None
    tags: Optional[list[str]] = None
    due_date: Optional[datetime] = None


class TaskResponse(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    status: str
    priority: str
    assignee: str
    project: str
    tags: list[str]
    due_date: Optional[datetime]
    created_at: datetime
    updated_at: datetime


TASK_COLUMNS = [
    "id", "title", "description", "status", "priority",
    "assignee", "project", "tags", "due_date", "created_at", "updated_at",
]
