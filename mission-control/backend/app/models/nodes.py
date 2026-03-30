"""Node models and constants."""

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class NodeStatus(str, Enum):
    online = "online"
    healthy = "healthy"
    degraded = "degraded"
    offline = "offline"


class NodeCreate(BaseModel):
    name: str
    hostname: str
    hardware: str = ""
    framework: str = ""
    status: NodeStatus = NodeStatus.offline
    ram_total_mb: int = 0
    ram_used_mb: int = 0
    cpu_percent: float = 0
    last_heartbeat: Optional[datetime] = None
    metadata: dict = Field(default_factory=dict)


class NodeUpdate(BaseModel):
    hostname: Optional[str] = None
    hardware: Optional[str] = None
    framework: Optional[str] = None
    status: Optional[NodeStatus] = None
    ram_total_mb: Optional[int] = None
    ram_used_mb: Optional[int] = None
    cpu_percent: Optional[float] = None
    last_heartbeat: Optional[datetime] = None
    metadata: Optional[dict] = None


class NodeResponse(BaseModel):
    id: uuid.UUID
    name: str
    hostname: str
    hardware: str
    framework: str
    status: NodeStatus
    ram_total_mb: int
    ram_used_mb: int
    cpu_percent: float
    last_heartbeat: Optional[datetime]
    metadata: dict
    created_at: datetime
    updated_at: datetime


NODE_COLUMNS = [
    "id", "name", "hostname", "hardware", "framework", "status",
    "ram_total_mb", "ram_used_mb", "cpu_percent", "last_heartbeat",
    "metadata", "created_at", "updated_at",
]
