"""Phase 8.0.1 — LIFE_LLM_DISABLED kill switch.

Two activation paths (either wins):
  - env var LIFE_LLM_DISABLED non-empty
  - sentinel file $LIFE_DIR/.llm-disabled present
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

CANONICAL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CANONICAL))

import life_kill_switch as lks  # noqa: E402


# ---------------------------------------------------------------- is_llm_disabled


def test_default_enabled(tmp_path, monkeypatch):
    monkeypatch.delenv("LIFE_LLM_DISABLED", raising=False)
    monkeypatch.setenv("LIFE_DIR", str(tmp_path))
    disabled, reason = lks.is_llm_disabled()
    assert disabled is False
    assert reason == ""


def test_env_var_activates(tmp_path, monkeypatch):
    monkeypatch.setenv("LIFE_LLM_DISABLED", "1")
    monkeypatch.setenv("LIFE_DIR", str(tmp_path))
    disabled, reason = lks.is_llm_disabled()
    assert disabled is True
    assert "LIFE_LLM_DISABLED" in reason


def test_env_var_non_empty_string(tmp_path, monkeypatch):
    """Any non-empty value counts."""
    monkeypatch.setenv("LIFE_LLM_DISABLED", "maintenance")
    monkeypatch.setenv("LIFE_DIR", str(tmp_path))
    disabled, reason = lks.is_llm_disabled()
    assert disabled is True
    assert "maintenance" in reason


def test_env_var_empty_string_does_not_activate(tmp_path, monkeypatch):
    monkeypatch.setenv("LIFE_LLM_DISABLED", "")
    monkeypatch.setenv("LIFE_DIR", str(tmp_path))
    disabled, _ = lks.is_llm_disabled()
    assert disabled is False


def test_sentinel_file_activates(tmp_path, monkeypatch):
    monkeypatch.delenv("LIFE_LLM_DISABLED", raising=False)
    monkeypatch.setenv("LIFE_DIR", str(tmp_path))
    (tmp_path / ".llm-disabled").write_text("")
    disabled, reason = lks.is_llm_disabled()
    assert disabled is True
    assert ".llm-disabled" in reason


def test_sentinel_file_content_ignored(tmp_path, monkeypatch):
    """Presence alone activates; content is ignored."""
    monkeypatch.delenv("LIFE_LLM_DISABLED", raising=False)
    monkeypatch.setenv("LIFE_DIR", str(tmp_path))
    (tmp_path / ".llm-disabled").write_text("any junk here")
    disabled, _ = lks.is_llm_disabled()
    assert disabled is True


def test_both_set_still_single_reason(tmp_path, monkeypatch):
    """Env var wins over sentinel when both present; no double-reporting."""
    monkeypatch.setenv("LIFE_LLM_DISABLED", "1")
    monkeypatch.setenv("LIFE_DIR", str(tmp_path))
    (tmp_path / ".llm-disabled").write_text("")
    disabled, reason = lks.is_llm_disabled()
    assert disabled is True
    # Env var is checked first -> reason mentions the env var, not the sentinel
    assert "LIFE_LLM_DISABLED" in reason
    assert ".llm-disabled" not in reason


def test_default_life_dir_when_env_unset(tmp_path, monkeypatch):
    """If LIFE_DIR is unset, falls back to ~/life — verify no crash."""
    monkeypatch.delenv("LIFE_DIR", raising=False)
    monkeypatch.delenv("LIFE_LLM_DISABLED", raising=False)
    # We can't control the real ~/life — just assert the call doesn't raise
    # and returns a bool.
    disabled, reason = lks.is_llm_disabled()
    assert isinstance(disabled, bool)
    assert isinstance(reason, str)


# ------------------------------------------------------ check_llm_kill_switch


def test_check_returns_empty_when_enabled(tmp_path, monkeypatch):
    monkeypatch.delenv("LIFE_LLM_DISABLED", raising=False)
    monkeypatch.setenv("LIFE_DIR", str(tmp_path))
    out = io.StringIO()
    reason = lks.check_llm_kill_switch(script="test", stream=out)
    assert reason == ""
    assert out.getvalue() == ""


def test_check_logs_and_returns_reason_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("LIFE_LLM_DISABLED", "1")
    monkeypatch.setenv("LIFE_DIR", str(tmp_path))
    out = io.StringIO()
    reason = lks.check_llm_kill_switch(script="lint_knowledge_llm", stream=out)
    assert reason
    log_line = out.getvalue()
    assert "[lint_knowledge_llm]" in log_line
    assert "LIFE_LLM_DISABLED" in log_line


def test_check_sentinel_reports_sentinel_reason(tmp_path, monkeypatch):
    monkeypatch.delenv("LIFE_LLM_DISABLED", raising=False)
    monkeypatch.setenv("LIFE_DIR", str(tmp_path))
    (tmp_path / ".llm-disabled").write_text("")
    out = io.StringIO()
    reason = lks.check_llm_kill_switch(script="rewrite_summaries", stream=out)
    assert ".llm-disabled" in reason
    assert ".llm-disabled" in out.getvalue()
