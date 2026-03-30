"""Service monitoring models."""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class ServiceStatus(str, Enum):
    up = "up"
    degraded = "degraded"
    down = "down"


class AlertStatus(str, Enum):
    down = "down"
    degraded = "degraded"
    recovered = "recovered"


class ServiceCheckIn(BaseModel):
    service: str
    status: ServiceStatus
    response_ms: Optional[int] = None
    error: Optional[str] = None
    checked_at: Optional[datetime] = None


class ServiceCheckBulk(BaseModel):
    checks: list[ServiceCheckIn]


class ServiceAlertIn(BaseModel):
    service: str
    status: AlertStatus
    message: Optional[str] = None
    downtime_seconds: Optional[int] = None
