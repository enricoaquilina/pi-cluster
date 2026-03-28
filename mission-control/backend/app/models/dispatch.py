"""Dispatch models."""

from typing import Optional

from pydantic import BaseModel, Field


class DispatchRequest(BaseModel):
    persona: str
    prompt: str
    timeout: int = Field(default=60, ge=5, le=120)


class DispatchResponse(BaseModel):
    persona: str
    node: str
    delegate: str
    response: str
    elapsed_ms: int
    fallback: bool = False
    original_node: Optional[str] = None


class DispatchLogEntry(BaseModel):
    persona: str = ""
    node: str = ""
    delegate: str = ""
    fallback: bool = False
    prompt_preview: str = ""
    response_preview: str = ""
    elapsed_ms: int = 0
    status: str = "success"
    error_detail: Optional[str] = None
