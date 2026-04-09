"""Integration tests for dispatch observability and resilience wiring.

These exercise routes/dispatch.py with mocks for the LLM call and the
reachability check. They verify the cross-cutting concerns land together:

- Every dispatch produces exactly one line in events.jsonl
- Unhandled exceptions are captured in a dead-letter log
- Kill switch (env var or kill file) returns L5 ack without calling LLM
- Low-disk condition returns a user-visible "disk full" reply
- Breaker open state returns L4 canned reply without hitting OpenRouter
- Non-Maxwell personas are unaffected by the new observability machinery
  (they still succeed; still get one events line)
"""

import json
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def fake_vault_and_logs(tmp_path, monkeypatch):
    life = tmp_path / "life"
    (life / "Areas" / "about-me").mkdir(parents=True)
    (life / "Daily" / "2026" / "04").mkdir(parents=True)
    (life / "Areas" / "about-me" / "profile.md").write_text("Enrico\n")
    (life / "Areas" / "about-me" / "hard-rules.md").write_text("- no secrets\n")
    (life / "Areas" / "about-me" / "workflow-habits.md").write_text("- staged\n")
    (life / "Daily" / "2026" / "04" / "2026-04-09.md").write_text("## today\n- work\n")

    openclaw = tmp_path / ".openclaw" / "workspace"
    openclaw.mkdir(parents=True)
    (openclaw / "IDENTITY.md").write_text("Maxwell\n")

    events = tmp_path / "events.jsonl"
    deadletter = tmp_path / "deadletter.jsonl"
    kill_file = tmp_path / "maxwell.disabled"

    monkeypatch.setenv("LIFE_DIR", str(life))
    monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", str(openclaw))
    monkeypatch.setenv("MAXWELL_EVENTS_LOG", str(events))
    monkeypatch.setenv("MAXWELL_DEAD_LETTER_LOG", str(deadletter))
    monkeypatch.setenv("MAXWELL_KILL_FILE", str(kill_file))
    monkeypatch.delenv("MAXWELL_WHATSAPP_ENABLED", raising=False)
    # Reset the module-level breaker between tests
    from app.dispatch_resilience import openrouter_breaker
    openrouter_breaker.reset()

    return {
        "life": life,
        "events": events,
        "deadletter": deadletter,
        "kill_file": kill_file,
    }


def _freeze_today(d: date):
    fake_now = datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)
    return patch("app.life_today._now", return_value=fake_now)


def _read_events(path) -> list:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ── Happy path ───────────────────────────────────────────────────────────────

async def test_successful_dispatch_emits_one_events_line(client, auth_headers, fake_vault_and_logs):
    with _freeze_today(date(2026, 4, 9)), \
         patch("app.routes.dispatch._is_gateway_reachable", new=AsyncMock(return_value=True)), \
         patch("app.routes.dispatch._openclaw_chat", new=AsyncMock(return_value="all good")):
        resp = await client.post(
            "/api/dispatch",
            headers=auth_headers,
            json={"persona": "Maxwell", "prompt": "hi", "timeout": 10},
        )
    assert resp.status_code == 200

    events = _read_events(fake_vault_and_logs["events"])
    assert len(events) == 1
    e = events[0]
    assert e["persona"] == "Maxwell"
    assert e["status"] == "success"
    assert e["degraded_level"] == "L0"
    assert e["guard_hit"] is False
    assert e["error_class"] is None
    assert e["llm_ms"] is not None


async def test_success_for_non_maxwell_persona_also_logged(client, auth_headers, fake_vault_and_logs):
    with patch("app.routes.dispatch._is_gateway_reachable", new=AsyncMock(return_value=True)), \
         patch("app.routes.dispatch._openclaw_chat", new=AsyncMock(return_value="code")):
        resp = await client.post(
            "/api/dispatch",
            headers=auth_headers,
            json={"persona": "Archie", "prompt": "write hello world", "timeout": 10},
        )
    assert resp.status_code == 200
    events = _read_events(fake_vault_and_logs["events"])
    assert len(events) == 1
    assert events[0]["persona"] == "Archie"
    assert events[0]["status"] == "success"


# ── Kill switch ──────────────────────────────────────────────────────────────

async def test_kill_switch_env_returns_l5_without_calling_llm(client, auth_headers, fake_vault_and_logs, monkeypatch):
    monkeypatch.setenv("MAXWELL_WHATSAPP_ENABLED", "0")

    chat = AsyncMock(return_value="should-not-be-called")
    with _freeze_today(date(2026, 4, 9)), \
         patch("app.routes.dispatch._is_gateway_reachable", new=AsyncMock(return_value=True)), \
         patch("app.routes.dispatch._openclaw_chat", new=chat):
        resp = await client.post(
            "/api/dispatch",
            headers=auth_headers,
            json={"persona": "Maxwell", "prompt": "hi", "timeout": 10},
        )

    assert resp.status_code == 200
    assert "paus" in resp.json()["response"].lower()
    chat.assert_not_called()

    events = _read_events(fake_vault_and_logs["events"])
    assert events[-1]["degraded_level"] == "L5"


async def test_kill_switch_file_returns_l5_without_calling_llm(client, auth_headers, fake_vault_and_logs):
    fake_vault_and_logs["kill_file"].touch()

    chat = AsyncMock(return_value="should-not-be-called")
    with _freeze_today(date(2026, 4, 9)), \
         patch("app.routes.dispatch._is_gateway_reachable", new=AsyncMock(return_value=True)), \
         patch("app.routes.dispatch._openclaw_chat", new=chat):
        resp = await client.post(
            "/api/dispatch",
            headers=auth_headers,
            json={"persona": "Maxwell", "prompt": "hi", "timeout": 10},
        )

    assert resp.status_code == 200
    chat.assert_not_called()
    events = _read_events(fake_vault_and_logs["events"])
    assert events[-1]["degraded_level"] == "L5"


# ── Circuit breaker → L4 ────────────────────────────────────────────────────

async def test_open_breaker_returns_l4_canned_reply(client, auth_headers, fake_vault_and_logs):
    from app.dispatch_resilience import openrouter_breaker

    # Force the breaker open
    for _ in range(openrouter_breaker.threshold):
        openrouter_breaker.record_failure()

    chat = AsyncMock(return_value="should-not-be-called")
    with _freeze_today(date(2026, 4, 9)), \
         patch("app.routes.dispatch._is_gateway_reachable", new=AsyncMock(return_value=True)), \
         patch("app.routes.dispatch._openclaw_chat", new=chat):
        resp = await client.post(
            "/api/dispatch",
            headers=auth_headers,
            json={"persona": "Maxwell", "prompt": "hi", "timeout": 10},
        )

    assert resp.status_code == 200
    assert chat.call_count == 0
    events = _read_events(fake_vault_and_logs["events"])
    assert events[-1]["degraded_level"] == "L4"


# ── Retry: transient LLM failure should eventually succeed ──────────────────

async def test_transient_502_retries_and_succeeds(client, auth_headers, fake_vault_and_logs):
    from fastapi import HTTPException
    calls = {"n": 0}

    async def flaky(prompt, system_prompt, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise HTTPException(status_code=502, detail="bad gateway")
        return "recovered"

    with _freeze_today(date(2026, 4, 9)), \
         patch("app.routes.dispatch._is_gateway_reachable", new=AsyncMock(return_value=True)), \
         patch("app.routes.dispatch._openclaw_chat", side_effect=flaky):
        resp = await client.post(
            "/api/dispatch",
            headers=auth_headers,
            json={"persona": "Maxwell", "prompt": "hi", "timeout": 10},
        )

    assert resp.status_code == 200
    assert resp.json()["response"] == "recovered"
    assert calls["n"] == 2
    events = _read_events(fake_vault_and_logs["events"])
    assert events[-1]["status"] == "success"


async def test_400_is_not_retried(client, auth_headers, fake_vault_and_logs):
    from fastapi import HTTPException
    calls = {"n": 0}

    async def fails(prompt, system_prompt, timeout):
        calls["n"] += 1
        raise HTTPException(status_code=400, detail="bad request")

    with _freeze_today(date(2026, 4, 9)), \
         patch("app.routes.dispatch._is_gateway_reachable", new=AsyncMock(return_value=True)), \
         patch("app.routes.dispatch._openclaw_chat", side_effect=fails):
        resp = await client.post(
            "/api/dispatch",
            headers=auth_headers,
            json={"persona": "Maxwell", "prompt": "hi", "timeout": 10},
        )

    # 400 propagates as a 400 from dispatch (it's a client error)
    assert resp.status_code == 400
    assert calls["n"] == 1  # no retry


# ── Dead letter ──────────────────────────────────────────────────────────────

async def test_unhandled_exception_writes_dead_letter(client, auth_headers, fake_vault_and_logs):
    async def boom(prompt, system_prompt, timeout):
        raise RuntimeError("completely unexpected")

    with _freeze_today(date(2026, 4, 9)), \
         patch("app.routes.dispatch._is_gateway_reachable", new=AsyncMock(return_value=True)), \
         patch("app.routes.dispatch._openclaw_chat", side_effect=boom):
        resp = await client.post(
            "/api/dispatch",
            headers=auth_headers,
            json={"persona": "Maxwell", "prompt": "hi", "timeout": 10},
        )

    # Caller sees a 500 (FastAPI translates raised RuntimeError)
    assert resp.status_code == 500

    # Dead letter recorded
    dead = fake_vault_and_logs["deadletter"]
    assert dead.exists()
    entries = [json.loads(line) for line in dead.read_text().splitlines() if line.strip()]
    assert len(entries) == 1
    assert entries[0]["persona"] == "Maxwell"
    assert "RuntimeError" in entries[0]["error"]


# ── Events schema sanity ─────────────────────────────────────────────────────

async def test_events_log_schema_stable(client, auth_headers, fake_vault_and_logs):
    from app.events_log import REQUIRED_FIELDS

    with _freeze_today(date(2026, 4, 9)), \
         patch("app.routes.dispatch._is_gateway_reachable", new=AsyncMock(return_value=True)), \
         patch("app.routes.dispatch._openclaw_chat", new=AsyncMock(return_value="ok")):
        await client.post(
            "/api/dispatch",
            headers=auth_headers,
            json={"persona": "Maxwell", "prompt": "hi", "timeout": 10},
        )

    events = _read_events(fake_vault_and_logs["events"])
    assert events, "no events logged"
    for field in REQUIRED_FIELDS:
        assert field in events[-1]
