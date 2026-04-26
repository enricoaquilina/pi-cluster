"""PRD (Product Requirements Document) models."""

from typing import Optional

from pydantic import BaseModel, Field


class PrdCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    title: str
    task_id: Optional[str] = None
    content: str
    model: str = "google/gemini-2.5-flash"


class PrdAction(BaseModel):
    feedback: Optional[str] = None


class PrdResponse(BaseModel):
    slug: str
    title: str
    task_id: Optional[str]
    content: str
    status: str
    feedback: Optional[str]
    model: str
    telegram_message_id: Optional[int]
    created_at: str
    updated_at: str
