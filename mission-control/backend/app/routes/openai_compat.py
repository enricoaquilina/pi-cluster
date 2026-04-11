"""OpenAI-compatible chat completions endpoint.

Wraps /api/dispatch so the OpenClaw gateway can route WhatsApp messages
through Mission Control by treating MC as just another model provider.

The gateway sends standard OpenAI chat completion requests, and this
endpoint translates them to internal dispatch calls, applying the full
MC pipeline: system prompt, rate limiting, prompt guard, outbound
redaction, and observability.

Usage in openclaw.json:
    Set a model provider's baseUrl to http://mission-control-proxy/api/openai-compat
    and the gateway will route through MC instead of calling OpenRouter directly.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..auth import verify_api_key

logger = logging.getLogger("mission-control")

router = APIRouter(prefix="/api/openai-compat", tags=["openai-compat"])


# ── Request / Response models (OpenAI-compatible subset) ────────────────────


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "maxwell"
    messages: List[ChatMessage]
    temperature: float = 0.7
    max_tokens: int = 4096


class ChatCompletionChoice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: str = "stop"


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatCompletionChoice]


# ── Endpoint ────────────────────────────────────────────────────────────────


@router.post("/chat/completions")
async def chat_completions(
    req: ChatCompletionRequest,
    _api_key: str = Depends(verify_api_key),
):
    """OpenAI-compatible chat completions that routes through /api/dispatch.

    Extracts the last user message and dispatches as the Maxwell persona
    through the full MC pipeline (system prompt, rate limiting, prompt guard,
    outbound redaction).
    """
    from .dispatch import _dispatch_internal

    # Extract the last user message as the prompt
    user_messages = [m for m in req.messages if m.role == "user"]
    prompt = user_messages[-1].content if user_messages else ""

    # Map model name to persona (default: Maxwell)
    persona = req.model if req.model != "maxwell" else "Maxwell"

    try:
        dispatch_response = await _dispatch_internal(
            persona=persona,
            prompt=prompt,
            timeout=60,
        )
        response_text = dispatch_response.response if dispatch_response else "I couldn't process that request."
    except Exception as e:
        logger.error("openai_compat: dispatch error: %s", e)
        response_text = "An error occurred processing your request."

    return ChatCompletionResponse(
        id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
        created=int(time.time()),
        model=req.model,
        choices=[
            ChatCompletionChoice(
                message=ChatMessage(role="assistant", content=response_text),
            )
        ],
    )
