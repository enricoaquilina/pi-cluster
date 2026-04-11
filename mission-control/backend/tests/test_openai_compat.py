"""Tests for OpenAI-compatible chat completions endpoint.

This endpoint wraps /api/dispatch so the OpenClaw gateway can route
WhatsApp messages through Mission Control's system prompt + redaction
pipeline by treating MC as just another model provider.
"""

import pytest


@pytest.mark.asyncio
async def test_chat_completions_returns_valid_format(client, auth_headers):
    """Response matches OpenAI chat completions schema."""
    resp = await client.post(
        "/api/openai-compat/chat/completions",
        headers=auth_headers,
        json={
            "model": "maxwell",
            "messages": [{"role": "user", "content": "What are my active projects?"}],
        },
    )
    # Should return 200 (even if LLM call fails, we return a valid response)
    assert resp.status_code == 200
    data = resp.json()
    # OpenAI format requires: id, object, choices
    assert "id" in data
    assert data["object"] == "chat.completion"
    assert "choices" in data
    assert len(data["choices"]) >= 1
    assert "message" in data["choices"][0]
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert isinstance(data["choices"][0]["message"]["content"], str)


@pytest.mark.asyncio
async def test_chat_completions_requires_auth(client):
    """Endpoint requires API key authentication."""
    resp = await client.post(
        "/api/openai-compat/chat/completions",
        json={
            "model": "maxwell",
            "messages": [{"role": "user", "content": "test"}],
        },
    )
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_chat_completions_requires_messages(client, auth_headers):
    """Request must include messages array."""
    resp = await client.post(
        "/api/openai-compat/chat/completions",
        headers=auth_headers,
        json={"model": "maxwell"},
    )
    assert resp.status_code == 422
