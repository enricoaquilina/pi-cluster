"""Structured dispatch events log — one JSONL line per dispatch.

Turns the dispatch path from opaque into queryable. Every dispatch (success,
failure, degraded, kill-switch, guard-hit) appends a single JSON object with
a fixed schema. Consumers: operators (``tail -f events.jsonl | jq``), SLIs
(cron queries), the ``maxwellctl`` CLI (future PR).

Design invariants tested here:

- One append per call; each append is a single ``\\n``-terminated JSON line
- File is created with mode 0600 (one user's dispatch history is sensitive)
- Parent directory is created with mode 0700 if missing
- Required schema fields are always present (even on degradation paths);
  missing values are ``null``, never absent
- Unwritable destination degrades silently (logs a warning, returns False)
  so a broken log directory never takes dispatch down
- Multiple lines in a single file are individually valid JSON
"""

import json
import os
import stat


def test_append_writes_valid_jsonl_line(tmp_path, monkeypatch):
    from app.events_log import EventLogger

    log_path = tmp_path / "events.jsonl"
    monkeypatch.setenv("MAXWELL_EVENTS_LOG", str(log_path))

    logger = EventLogger()
    ok = logger.append({"persona": "Maxwell", "status": "success", "llm_ms": 42})
    assert ok is True

    assert log_path.exists()
    lines = log_path.read_text().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["persona"] == "Maxwell"
    assert parsed["status"] == "success"
    assert parsed["llm_ms"] == 42


def test_multiple_appends_produce_multiple_lines(tmp_path, monkeypatch):
    from app.events_log import EventLogger

    log_path = tmp_path / "events.jsonl"
    monkeypatch.setenv("MAXWELL_EVENTS_LOG", str(log_path))

    logger = EventLogger()
    for i in range(5):
        logger.append({"persona": "Maxwell", "i": i})

    lines = log_path.read_text().splitlines()
    assert len(lines) == 5
    for i, line in enumerate(lines):
        parsed = json.loads(line)
        assert parsed["i"] == i


def test_required_fields_always_present(tmp_path, monkeypatch):
    """Every event has ts, persona, status (even if nullable)."""
    from app.events_log import EventLogger, REQUIRED_FIELDS

    log_path = tmp_path / "events.jsonl"
    monkeypatch.setenv("MAXWELL_EVENTS_LOG", str(log_path))

    logger = EventLogger()
    logger.append({"persona": "Maxwell"})  # minimal payload

    parsed = json.loads(log_path.read_text().splitlines()[0])
    for field in REQUIRED_FIELDS:
        assert field in parsed, f"required field missing: {field}"


def test_ts_auto_populated_if_not_provided(tmp_path, monkeypatch):
    from app.events_log import EventLogger

    log_path = tmp_path / "events.jsonl"
    monkeypatch.setenv("MAXWELL_EVENTS_LOG", str(log_path))

    logger = EventLogger()
    logger.append({"persona": "Maxwell"})

    parsed = json.loads(log_path.read_text().splitlines()[0])
    assert parsed["ts"] is not None
    # ISO-8601 format
    assert "T" in parsed["ts"]


def test_file_mode_0600(tmp_path, monkeypatch):
    from app.events_log import EventLogger

    log_path = tmp_path / "events.jsonl"
    monkeypatch.setenv("MAXWELL_EVENTS_LOG", str(log_path))

    logger = EventLogger()
    logger.append({"persona": "Maxwell"})

    mode = stat.S_IMODE(os.stat(log_path).st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_parent_dir_auto_created_with_0700(tmp_path, monkeypatch):
    from app.events_log import EventLogger

    log_path = tmp_path / "nested" / "dir" / "events.jsonl"
    monkeypatch.setenv("MAXWELL_EVENTS_LOG", str(log_path))

    logger = EventLogger()
    assert logger.append({"persona": "Maxwell"}) is True

    assert log_path.exists()
    parent_mode = stat.S_IMODE(os.stat(log_path.parent).st_mode)
    assert parent_mode == 0o700, f"expected 0700 on parent, got {oct(parent_mode)}"


def test_unwritable_path_degrades_silently(monkeypatch):
    """A broken log directory must not take dispatch down."""
    from app.events_log import EventLogger

    # /proc is not writable as a regular user; any attempt raises OSError.
    monkeypatch.setenv("MAXWELL_EVENTS_LOG", "/proc/nonexistent/events.jsonl")

    logger = EventLogger()
    ok = logger.append({"persona": "Maxwell"})
    assert ok is False  # signals failure without raising


def test_nested_objects_serialized(tmp_path, monkeypatch):
    from app.events_log import EventLogger

    log_path = tmp_path / "events.jsonl"
    monkeypatch.setenv("MAXWELL_EVENTS_LOG", str(log_path))

    logger = EventLogger()
    logger.append({
        "persona": "Maxwell",
        "breaker": {"state": "closed", "fails": 0},
        "tool_calls": [],
    })

    parsed = json.loads(log_path.read_text().splitlines()[0])
    assert parsed["breaker"]["state"] == "closed"
    assert parsed["tool_calls"] == []


def test_default_path_respects_env(tmp_path, monkeypatch):
    """MAXWELL_EVENTS_LOG is the single configuration knob."""
    from app.events_log import resolve_log_path

    monkeypatch.setenv("MAXWELL_EVENTS_LOG", str(tmp_path / "custom.jsonl"))
    assert resolve_log_path() == str(tmp_path / "custom.jsonl")


def test_newline_in_content_does_not_break_jsonl(tmp_path, monkeypatch):
    """User prompts may contain newlines; they must be escaped in JSON, not
    broken into multiple JSONL records."""
    from app.events_log import EventLogger

    log_path = tmp_path / "events.jsonl"
    monkeypatch.setenv("MAXWELL_EVENTS_LOG", str(log_path))

    logger = EventLogger()
    logger.append({"persona": "Maxwell", "prompt_preview": "line1\nline2\nline3"})

    lines = log_path.read_text().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["prompt_preview"] == "line1\nline2\nline3"
