"""Integration test: POST /api/dispatch with persona='Maxwell' must inject the
rich system prompt and apply the outbound guard to the response.

These tests mock the OpenRouter HTTP call and the gateway reachability check
so they don't need network or a live LLM. They do depend on the FastAPI app +
conftest's Postgres fixtures.
"""

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def fake_vault_env(tmp_path, monkeypatch):
    """Set env vars so the prompt builder reads from a tmp vault."""
    life = tmp_path / "life"
    openclaw = tmp_path / ".openclaw" / "workspace"
    (life / "Areas" / "about-me").mkdir(parents=True)
    (life / "Daily" / "2026" / "04").mkdir(parents=True)
    openclaw.mkdir(parents=True)

    (life / "Areas" / "about-me" / "profile.md").write_text("Name: Enrico\n")
    (life / "Areas" / "about-me" / "hard-rules.md").write_text("- No secrets in chat\n")
    (life / "Areas" / "about-me" / "workflow-habits.md").write_text("- Staged migrations\n")
    (openclaw / "IDENTITY.md").write_text("Maxwell. Detail-oriented.\n")
    (life / "Daily" / "2026" / "04" / "2026-04-09.md").write_text(
        "## Active Projects\n- pi-cluster: hardening the watchdog\n"
    )

    monkeypatch.setenv("LIFE_DIR", str(life))
    monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", str(openclaw))
    return life


def _freeze_today(d: date):
    fake_now = datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)
    return patch("app.life_today._now", return_value=fake_now)


async def test_maxwell_persona_is_accepted_by_dispatch(client, auth_headers, fake_vault_env):
    """POST /api/dispatch with persona='Maxwell' no longer returns 400."""
    with _freeze_today(date(2026, 4, 9)), \
         patch("app.routes.dispatch._is_gateway_reachable", new=AsyncMock(return_value=True)), \
         patch("app.routes.dispatch._openclaw_chat", new=AsyncMock(return_value="ok")):
        resp = await client.post(
            "/api/dispatch",
            headers=auth_headers,
            json={"persona": "Maxwell", "prompt": "what progress today?", "timeout": 10},
        )

    assert resp.status_code != 400, f"Maxwell persona rejected: {resp.text}"
    assert resp.status_code == 200


async def test_maxwell_dispatch_injects_rich_system_prompt(client, auth_headers, fake_vault_env):
    """The system_prompt passed to _openclaw_chat includes vault content + grounding."""
    captured = {}

    async def fake_chat(prompt, system_prompt, timeout):
        captured["prompt"] = prompt
        captured["system_prompt"] = system_prompt
        return "ok"

    with _freeze_today(date(2026, 4, 9)), \
         patch("app.routes.dispatch._is_gateway_reachable", new=AsyncMock(return_value=True)), \
         patch("app.routes.dispatch._openclaw_chat", side_effect=fake_chat):
        await client.post(
            "/api/dispatch",
            headers=auth_headers,
            json={"persona": "Maxwell", "prompt": "what progress today?", "timeout": 10},
        )

    sp = captured.get("system_prompt", "")
    # Daily-note content
    assert "pi-cluster: hardening the watchdog" in sp
    # Hard rules
    assert "No secrets in chat" in sp
    # Today's date
    assert "2026-04-09" in sp
    # Grounding header forbids the hallucinated path
    assert "~/life/" in sp
    # vault-file fencing
    assert "<vault-file path=" in sp


async def test_maxwell_dispatch_wraps_user_prompt_as_untrusted(client, auth_headers, fake_vault_env):
    """The user prompt is wrapped in <untrusted_user_message> before being sent."""
    captured = {}

    async def fake_chat(prompt, system_prompt, timeout):
        captured["prompt"] = prompt
        return "ok"

    with _freeze_today(date(2026, 4, 9)), \
         patch("app.routes.dispatch._is_gateway_reachable", new=AsyncMock(return_value=True)), \
         patch("app.routes.dispatch._openclaw_chat", side_effect=fake_chat):
        await client.post(
            "/api/dispatch",
            headers=auth_headers,
            json={"persona": "Maxwell", "prompt": "what progress today?", "timeout": 10},
        )

    wrapped = captured.get("prompt", "")
    assert "<untrusted_user_message>" in wrapped
    assert "</untrusted_user_message>" in wrapped
    assert "what progress today?" in wrapped


async def test_maxwell_response_passes_through_outbound_guard(
    client, auth_headers, fake_vault_env
):
    """If the LLM returns a hallucinated path, the guard replaces it before response."""
    bad_reply = (
        "Looking in ~/.openclaw/workspace/memory/ for your note..."
    )

    with _freeze_today(date(2026, 4, 9)), \
         patch("app.routes.dispatch._is_gateway_reachable", new=AsyncMock(return_value=True)), \
         patch("app.routes.dispatch._openclaw_chat", new=AsyncMock(return_value=bad_reply)):
        resp = await client.post(
            "/api/dispatch",
            headers=auth_headers,
            json={"persona": "Maxwell", "prompt": "what progress today?", "timeout": 10},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "~/.openclaw/workspace/memory" not in data["response"]


async def test_maxwell_dispatch_preserves_persona_casing(client, auth_headers, fake_vault_env):
    """Per-persona IDENTITY.md (e.g. workspace/Maxwell/IDENTITY.md) must be found.

    Regression guard: a previous revision lowercased req.persona before passing
    it to build_system_prompt, which broke the per-persona IDENTITY directory
    lookup in _identity_segment(). Ensure we preserve the exact casing.
    """
    # Create per-persona identity dir with the exact 'Maxwell' casing
    openclaw_workspace = fake_vault_env.parent / ".openclaw" / "workspace"
    (openclaw_workspace / "Maxwell").mkdir(parents=True, exist_ok=True)
    (openclaw_workspace / "Maxwell" / "IDENTITY.md").write_text(
        "# Maxwell cased identity marker\nPER_PERSONA_SENTINEL=abc123\n"
    )

    captured = {}

    async def fake_chat(prompt, system_prompt, timeout):
        captured["system_prompt"] = system_prompt
        return "ok"

    with _freeze_today(date(2026, 4, 9)), \
         patch("app.routes.dispatch._is_gateway_reachable", new=AsyncMock(return_value=True)), \
         patch("app.routes.dispatch._openclaw_chat", side_effect=fake_chat):
        await client.post(
            "/api/dispatch",
            headers=auth_headers,
            json={"persona": "Maxwell", "prompt": "hi", "timeout": 10},
        )

    sp = captured.get("system_prompt", "")
    # The per-persona IDENTITY.md under workspace/Maxwell/ must have been read,
    # proving the casing was preserved (workspace/maxwell/ wouldn't exist).
    assert "PER_PERSONA_SENTINEL=abc123" in sp


async def test_non_maxwell_persona_unchanged(client, auth_headers, fake_vault_env):
    """Existing personas (Archie, Pixel, ...) still use their static system prompts."""
    captured = {}

    async def fake_chat(prompt, system_prompt, timeout):
        captured["system_prompt"] = system_prompt
        captured["prompt"] = prompt
        return "ok"

    with patch("app.routes.dispatch._is_gateway_reachable", new=AsyncMock(return_value=True)), \
         patch("app.routes.dispatch._openclaw_chat", side_effect=fake_chat):
        await client.post(
            "/api/dispatch",
            headers=auth_headers,
            json={"persona": "Archie", "prompt": "write a fastapi endpoint", "timeout": 10},
        )

    sp = captured.get("system_prompt", "")
    assert "Archie" in sp
    assert "backend developer" in sp
    # Unchanged personas are not wrapped in untrusted tags either — that's Maxwell-only.
    assert captured.get("prompt") == "write a fastapi endpoint"
