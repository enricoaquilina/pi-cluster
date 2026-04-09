"""Graceful degradation ladder (L0–L5).

When something is broken, Maxwell should still be useful — or at least fail
in a way the user understands. The ladder defines five fall-back levels
below full service:

- L0 full: everything works
- L1 no QMD: future, skip tool loop
- L2 no daily note: today's file unreadable → stubbed in prompt (already
  handled by maxwell_prompt.py) but degradation is recorded
- L3 no vault: ~/life/ inaccessible → minimal hardcoded identity prompt
- L4 no LLM: OpenRouter breaker open → canned reply, no network call
- L5 kill switch: MAXWELL_WHATSAPP_ENABLED=0 or kill-file present → pause ack

Each level has a stable string identifier recorded in events.jsonl so we can
count "days since last degradation" as an SLI.
"""


def test_level_enum_has_six_values():
    from app.dispatch_degradation import DegradationLevel

    values = {level.value for level in DegradationLevel}
    assert values == {"L0", "L1", "L2", "L3", "L4", "L5"}


def test_l0_reply_is_none():
    """L0 means no canned reply — the real LLM call proceeds."""
    from app.dispatch_degradation import DegradationLevel, degraded_reply

    assert degraded_reply(DegradationLevel.L0) is None


def test_l4_reply_mentions_llm_unreachable():
    """L4 = OpenRouter breaker open. Tell the user without exposing internals."""
    from app.dispatch_degradation import DegradationLevel, degraded_reply

    reply = degraded_reply(DegradationLevel.L4)
    assert reply is not None
    low = reply.lower()
    # User-facing, not technical
    assert "later" in low or "momentarily" in low or "unavailable" in low


def test_l5_reply_mentions_paused():
    from app.dispatch_degradation import DegradationLevel, degraded_reply

    reply = degraded_reply(DegradationLevel.L5)
    assert reply is not None
    assert "paus" in reply.lower()


def test_l3_reply_minimal_not_empty():
    from app.dispatch_degradation import DegradationLevel, degraded_reply

    reply = degraded_reply(DegradationLevel.L3)
    assert reply is not None
    assert len(reply) > 0


def test_compute_degradation_default_is_l0(tmp_path, monkeypatch):
    """Default — no kill-switch, vault present, breaker closed → L0."""
    from app.dispatch_degradation import DegradationLevel, compute_degradation

    # Setup a vault dir to pass the L3 check
    life = tmp_path / "life"
    life.mkdir()
    monkeypatch.setenv("LIFE_DIR", str(life))

    level = compute_degradation(
        kill_switch_env="",
        kill_switch_file=str(tmp_path / "not-a-real-file"),
        breaker_open=False,
    )
    assert level == DegradationLevel.L0


def test_compute_degradation_kill_switch_env_gives_l5(tmp_path, monkeypatch):
    from app.dispatch_degradation import DegradationLevel, compute_degradation

    life = tmp_path / "life"
    life.mkdir()
    monkeypatch.setenv("LIFE_DIR", str(life))

    level = compute_degradation(
        kill_switch_env="0",
        kill_switch_file=str(tmp_path / "no"),
        breaker_open=False,
    )
    assert level == DegradationLevel.L5


def test_compute_degradation_kill_file_gives_l5(tmp_path, monkeypatch):
    from app.dispatch_degradation import DegradationLevel, compute_degradation

    life = tmp_path / "life"
    life.mkdir()
    monkeypatch.setenv("LIFE_DIR", str(life))

    kill_file = tmp_path / "maxwell.disabled"
    kill_file.touch()

    level = compute_degradation(
        kill_switch_env="",
        kill_switch_file=str(kill_file),
        breaker_open=False,
    )
    assert level == DegradationLevel.L5


def test_compute_degradation_breaker_open_gives_l4(tmp_path, monkeypatch):
    from app.dispatch_degradation import DegradationLevel, compute_degradation

    life = tmp_path / "life"
    life.mkdir()
    monkeypatch.setenv("LIFE_DIR", str(life))

    level = compute_degradation(
        kill_switch_env="",
        kill_switch_file=str(tmp_path / "no"),
        breaker_open=True,
    )
    assert level == DegradationLevel.L4


def test_compute_degradation_missing_vault_gives_l3(tmp_path, monkeypatch):
    from app.dispatch_degradation import DegradationLevel, compute_degradation

    # Point LIFE_DIR at a nonexistent path
    monkeypatch.setenv("LIFE_DIR", str(tmp_path / "no-vault"))

    level = compute_degradation(
        kill_switch_env="",
        kill_switch_file=str(tmp_path / "no"),
        breaker_open=False,
    )
    assert level == DegradationLevel.L3


def test_kill_switch_precedence_over_breaker(tmp_path, monkeypatch):
    """L5 (kill) wins over L4 (breaker) — when both trigger, report L5."""
    from app.dispatch_degradation import DegradationLevel, compute_degradation

    life = tmp_path / "life"
    life.mkdir()
    monkeypatch.setenv("LIFE_DIR", str(life))

    level = compute_degradation(
        kill_switch_env="0",
        kill_switch_file=str(tmp_path / "no"),
        breaker_open=True,
    )
    assert level == DegradationLevel.L5


def test_level_value_round_trips():
    """Level identifiers are stable strings usable in events.jsonl."""
    from app.dispatch_degradation import DegradationLevel

    for level in DegradationLevel:
        assert DegradationLevel(level.value) is level
