"""Regression guards for the AI review nits on #138.

Three importance-5 / importance-7 catches, each tested here:

1. ``call_with_retry`` must NOT swallow ``BaseException`` (KeyboardInterrupt,
   SystemExit) — those should propagate cleanly so ctrl-C / shutdown works.
2. ``CircuitBreaker.allow`` in the open state must not rely on ``assert``
   (it's compiled out under ``python -O``) — it should either handle the
   missing timestamp explicitly or raise a clear error.
3. ``_append_dead_letter`` must catch any exception from the serializer or
   the file I/O path, not just ``OSError``. Otherwise an unexpected error
   inside dead-letter handling masks the original dispatch error that
   triggered dead-letter in the first place.
"""

import json
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest


# ── Nit 1: BaseException must propagate ─────────────────────────────────────

async def test_call_with_retry_propagates_keyboard_interrupt():
    """KeyboardInterrupt must NOT be caught by the retry loop."""
    from app.dispatch_resilience import call_with_retry

    async def ctrl_c():
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        await call_with_retry(
            ctrl_c,
            max_retries=3,
            backoff_ms=1,
            is_retryable=lambda e: True,  # even if classified retryable
        )


async def test_call_with_retry_propagates_system_exit():
    """SystemExit must NOT be caught by the retry loop."""
    from app.dispatch_resilience import call_with_retry

    async def exiting():
        raise SystemExit(1)

    with pytest.raises(SystemExit):
        await call_with_retry(
            exiting,
            max_retries=3,
            backoff_ms=1,
            is_retryable=lambda e: True,
        )


async def test_call_with_retry_still_retries_regular_exceptions():
    """Regression guard: broadening BaseException→Exception must not break
    the existing retry-on-regular-exception behavior."""
    from app.dispatch_resilience import call_with_retry

    class Transient(Exception):
        pass

    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise Transient("boom")
        return "ok"

    result = await call_with_retry(
        flaky,
        max_retries=1,
        backoff_ms=1,
        is_retryable=lambda e: isinstance(e, Transient),
    )
    assert result == "ok"
    assert calls["n"] == 2


# ── Nit 2: CircuitBreaker.allow must not rely on assert ─────────────────────

def test_breaker_allow_in_open_state_works_under_optimization():
    """``assert`` is compiled out under ``python -O``; ``allow()`` must still
    return a sensible bool even when the assert would have been a no-op."""
    from app.dispatch_resilience import CircuitBreaker

    b = CircuitBreaker(threshold=1, window_seconds=60, cooldown_seconds=60)
    b.record_failure()
    assert b.state == "open"

    # Call allow() — it used to `assert self._opened_at is not None`. Even
    # under -O the call must return False (cooldown not elapsed) without
    # raising AssertionError or a bare TypeError on None arithmetic.
    result = b.allow()
    assert result is False
    assert b.state == "open"


def test_breaker_allow_raises_clear_error_if_state_corrupted():
    """Defense-in-depth: if _opened_at is somehow None in the open state,
    the breaker raises a descriptive RuntimeError instead of AssertionError
    or a cryptic TypeError from None subtraction."""
    from app.dispatch_resilience import CircuitBreaker

    b = CircuitBreaker(threshold=1, window_seconds=60, cooldown_seconds=60)
    b.record_failure()
    assert b.state == "open"

    # Corrupt the invariant manually.
    b._opened_at = None
    with pytest.raises(RuntimeError, match="opened_at"):
        b.allow()


# ── Nit 3: _append_dead_letter must swallow ANY exception ───────────────────

@pytest.fixture
def fake_vault_for_deadletter(tmp_path, monkeypatch):
    life = tmp_path / "life"
    (life / "Areas" / "about-me").mkdir(parents=True)
    (life / "Daily" / "2026" / "04").mkdir(parents=True)
    for f in ("profile.md", "hard-rules.md", "workflow-habits.md"):
        (life / "Areas" / "about-me" / f).write_text(f"# {f}\n")
    (life / "Daily" / "2026" / "04" / "2026-04-09.md").write_text("# today\n")

    openclaw = tmp_path / ".openclaw" / "workspace"
    openclaw.mkdir(parents=True)
    (openclaw / "IDENTITY.md").write_text("Maxwell\n")

    monkeypatch.setenv("LIFE_DIR", str(life))
    monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", str(openclaw))
    monkeypatch.setenv("MAXWELL_EVENTS_LOG", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("MAXWELL_DEAD_LETTER_LOG", str(tmp_path / "deadletter.jsonl"))
    monkeypatch.setenv("MAXWELL_KILL_FILE", str(tmp_path / "no-kill"))
    monkeypatch.delenv("MAXWELL_WHATSAPP_ENABLED", raising=False)

    from app.dispatch_resilience import openrouter_breaker
    openrouter_breaker.reset()
    return tmp_path


def _freeze_today(d: date):
    fake_now = datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)
    return patch("app.life_today._now", return_value=fake_now)


async def test_dead_letter_swallows_type_error_from_serializer(
    client, auth_headers, fake_vault_for_deadletter, caplog
):
    """If json.dumps raises a TypeError inside _append_dead_letter (e.g. a
    non-serializable payload), the dispatch handler must still return a
    clean HTTPException(500). The dead-letter failure must not mask the
    original RuntimeError."""

    async def boom(prompt, system_prompt, timeout, model=None):
        raise RuntimeError("original dispatch error")

    real_dumps = json.dumps

    def broken_dumps(*args, **kwargs):
        # Break serialization only for dead-letter entries (they include
        # 'error' keyword); leave other JSON alone.
        if args and isinstance(args[0], dict) and "error" in args[0]:
            raise TypeError("cannot serialize custom object")
        return real_dumps(*args, **kwargs)

    with _freeze_today(date(2026, 4, 9)), \
         patch("app.routes.dispatch._is_gateway_reachable", new=AsyncMock(return_value=True)), \
         patch("app.routes.dispatch._openclaw_chat", side_effect=boom), \
         patch("app.routes.dispatch.json.dumps", side_effect=broken_dumps):
        resp = await client.post(
            "/api/dispatch",
            headers=auth_headers,
            json={"persona": "Maxwell", "prompt": "hi", "timeout": 10},
        )

    # Original RuntimeError was translated to HTTPException(500), not
    # replaced by a TypeError from the broken serializer.
    assert resp.status_code == 500
    assert "internal dispatch error" in resp.json()["detail"]


# The TypeError test above already exercises the broad `except Exception`
# in _append_dead_letter by forcing json.dumps to raise a non-OSError. Adding
# a second test that patches os.open globally would also affect pytest's own
# internal file handling, so one focused test is enough for this nit.
