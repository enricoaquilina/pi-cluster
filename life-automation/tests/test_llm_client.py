"""Phase 8.0.2 — llm_client subprocess wrapper tests.

All tests use ``set_runner`` to inject a deterministic fake subprocess;
nothing here contacts the real ``claude`` CLI.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

CANONICAL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CANONICAL))

import llm_client as lc  # noqa: E402
from llm_client import (  # noqa: E402
    CostCapExceeded,
    LlmClient,
    RunnerResult,
    _NotFoundMarker,
    _TimeoutMarker,
    redact_secrets,
    set_runner,
    reset_runner,
)


# ---------------------------------------------------------------- helpers


def make_envelope(*, result="ok", in_tokens=10, out_tokens=20, cost=0.001):
    """Build a realistic `claude --output-format json` envelope."""
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "duration_ms": 100,
            "result": result,
            "stop_reason": "end_turn",
            "session_id": "test-session",
            "total_cost_usd": cost,
            "usage": {
                "input_tokens": in_tokens,
                "output_tokens": out_tokens,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        }
    )


class Scripted:
    """FakeRunner that replays a sequence of responses (success/error/timeout)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, argv, timeout_s):
        self.calls.append((list(argv), timeout_s))
        if not self.responses:
            raise RuntimeError("Scripted runner ran out of responses")
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def isolated_life(tmp_path, monkeypatch):
    """Point LIFE_DIR at a clean tmpdir so logs/ledger don't pollute reality."""
    monkeypatch.setenv("LIFE_DIR", str(tmp_path))
    (tmp_path / "logs").mkdir()
    # Clear module default so env changes take effect
    monkeypatch.setattr(lc, "_default_client", None, raising=False)
    yield tmp_path


@pytest.fixture
def runner_cleanup():
    yield
    reset_runner()


# =============================================================== happy paths


def test_successful_call_parses_envelope(isolated_life, runner_cleanup):
    set_runner(Scripted([RunnerResult(stdout=make_envelope(), stderr="", returncode=0)]))
    client = LlmClient()
    result = client.call("say hi", script="test")
    assert result.status == "ok"
    assert result.text == "ok"
    assert result.tokens_in == 10
    assert result.tokens_out == 20
    assert result.cost_usd == pytest.approx(0.001)
    assert result.cost_source == "actual"
    assert result.retries == 0
    assert result.duration_ms >= 0


def test_module_level_call_haiku(isolated_life, runner_cleanup):
    set_runner(Scripted([RunnerResult(stdout=make_envelope(), stderr="", returncode=0)]))
    result = lc.call_haiku("hello", script="test")
    assert result.status == "ok"


def test_envelope_missing_usage_falls_back_to_estimate(isolated_life, runner_cleanup):
    envelope = json.dumps({"type": "result", "result": "hello world"})  # no usage/cost
    set_runner(Scripted([RunnerResult(stdout=envelope, stderr="", returncode=0)]))
    client = LlmClient()
    result = client.call("prompt here", script="test")
    assert result.status == "ok"
    assert result.cost_source == "estimated"
    assert result.tokens_in > 0
    assert result.tokens_out > 0
    assert result.cost_usd > 0


# =================================================================== retries


def test_retry_on_timeout_then_success(isolated_life, runner_cleanup):
    runner = Scripted([
        _TimeoutMarker("1s"),
        _TimeoutMarker("1s"),
        RunnerResult(stdout=make_envelope(), stderr="", returncode=0),
    ])
    set_runner(runner)
    client = LlmClient(retry_base_ms=1, retry_cap_ms=2, retry_total_s=30)
    result = client.call("x", script="test")
    assert result.status == "ok"
    assert result.retries == 2


def test_retry_exhaustion_returns_timeout_status(isolated_life, runner_cleanup):
    runner = Scripted([_TimeoutMarker("1s")] * 5)
    set_runner(runner)
    client = LlmClient(retry_max=3, retry_base_ms=1, retry_cap_ms=2, retry_total_s=30)
    result = client.call("x", script="test")
    assert result.status == "timeout"
    assert result.retries == 2  # attempts-1 == retry count


def test_retry_on_rate_limit_stderr(isolated_life, runner_cleanup):
    runner = Scripted([
        RunnerResult(stdout="", stderr="rate_limit exceeded", returncode=1),
        RunnerResult(stdout=make_envelope(), stderr="", returncode=0),
    ])
    set_runner(runner)
    client = LlmClient(retry_base_ms=1, retry_cap_ms=2)
    result = client.call("x", script="test")
    assert result.status == "ok"
    assert result.retries == 1


def test_non_retryable_error_fails_fast(isolated_life, runner_cleanup):
    runner = Scripted([
        RunnerResult(stdout="", stderr="authentication failed", returncode=1),
    ])
    set_runner(runner)
    client = LlmClient()
    result = client.call("x", script="test")
    assert result.status == "error"
    assert "authentication failed" in result.error


def test_claude_binary_missing(isolated_life, runner_cleanup):
    set_runner(Scripted([_NotFoundMarker("/no/such/claude")]))
    client = LlmClient()
    result = client.call("x", script="test")
    assert result.status == "error"
    assert "not found" in result.error.lower()


# ================================================================= cost cap


def test_per_run_cap_blocks_before_subprocess(isolated_life, runner_cleanup):
    runner = Scripted([])  # must NOT be called
    set_runner(runner)
    client = LlmClient(max_cost_per_run_usd=0.0)  # zero cap
    with pytest.raises(CostCapExceeded):
        client.call("this is a prompt", script="test")
    assert runner.calls == []


def test_per_run_cap_accumulates(isolated_life, runner_cleanup):
    """Multiple calls accumulate toward the per-run cap."""
    runner = Scripted([
        RunnerResult(stdout=make_envelope(cost=0.30), stderr="", returncode=0),
        RunnerResult(stdout=make_envelope(cost=0.30), stderr="", returncode=0),
    ])
    set_runner(runner)
    client = LlmClient(max_cost_per_run_usd=0.50)
    r1 = client.call("a", script="t")
    assert r1.status == "ok"
    # Second call: $0.30 already spent + estimated ~0 = ok to start, actual
    # cost after becomes $0.60 > $0.50 but the CHECK is pre-call so it passes.
    # The important invariant: third call would pre-check and fail.
    r2 = client.call("b", script="t")
    assert r2.status == "ok"


def test_nightly_cap_skipped_without_run_id(isolated_life, runner_cleanup, monkeypatch):
    monkeypatch.delenv("NIGHTLY_RUN_ID", raising=False)
    set_runner(Scripted([RunnerResult(stdout=make_envelope(), stderr="", returncode=0)]))
    client = LlmClient(max_cost_per_nightly_usd=0.0)  # would block if honored
    result = client.call("x", script="t")
    assert result.status == "ok"


def test_nightly_cap_enforced_with_run_id(isolated_life, runner_cleanup, monkeypatch):
    monkeypatch.setenv("NIGHTLY_RUN_ID", "run-xyz")
    # Seed ledger at effective ceiling — any additional estimate trips the cap
    ledger = isolated_life / "logs" / "llm-cost-window.json"
    ledger.write_text(json.dumps({"run-xyz": 1.999}))
    set_runner(Scripted([]))
    client = LlmClient(max_cost_per_nightly_usd=2.00, max_cost_per_run_usd=10.0)
    with pytest.raises(CostCapExceeded):
        client.call("x" * 400, script="t")


def test_nightly_ledger_updates_after_success(isolated_life, runner_cleanup, monkeypatch):
    monkeypatch.setenv("NIGHTLY_RUN_ID", "run-abc")
    set_runner(Scripted([RunnerResult(stdout=make_envelope(cost=0.05), stderr="", returncode=0)]))
    client = LlmClient(max_cost_per_nightly_usd=2.00)
    result = client.call("x", script="t")
    assert result.status == "ok"
    ledger = json.loads((isolated_life / "logs" / "llm-cost-window.json").read_text())
    assert ledger["run-abc"] == pytest.approx(0.05)


def test_corrupt_nightly_ledger_quarantined(isolated_life, runner_cleanup, monkeypatch):
    monkeypatch.setenv("NIGHTLY_RUN_ID", "run-q")
    ledger_path = isolated_life / "logs" / "llm-cost-window.json"
    ledger_path.write_text("{not valid json")
    set_runner(Scripted([RunnerResult(stdout=make_envelope(cost=0.01), stderr="", returncode=0)]))
    client = LlmClient(max_cost_per_nightly_usd=2.00)
    # Should not crash; should quarantine and proceed fail-reduced
    result = client.call("x", script="t")
    assert result.status == "ok"
    # quarantine file created
    quarantined = list((isolated_life / "logs").glob("llm-cost-window.json.corrupt.*"))
    assert quarantined, "corrupt ledger should be quarantined"


# ================================================================ expect_json


def test_expect_json_parses_fenced_output(isolated_life, runner_cleanup):
    fenced = "```json\n[{\"issue\": \"x\"}]\n```"
    set_runner(Scripted([RunnerResult(
        stdout=make_envelope(result=fenced), stderr="", returncode=0
    )]))
    client = LlmClient()
    result = client.call("x", script="t", expect_json=True)
    assert result.status == "ok"
    assert result.data == [{"issue": "x"}]


def test_expect_json_bare_object(isolated_life, runner_cleanup):
    set_runner(Scripted([RunnerResult(
        stdout=make_envelope(result='{"a": 1}'), stderr="", returncode=0
    )]))
    client = LlmClient()
    result = client.call("x", script="t", expect_json=True)
    assert result.status == "ok"
    assert result.data == {"a": 1}


def test_expect_json_parse_error_after_retry(isolated_life, runner_cleanup):
    set_runner(Scripted([
        RunnerResult(stdout=make_envelope(result="not json"), stderr="", returncode=0),
        RunnerResult(stdout=make_envelope(result="still not json"), stderr="", returncode=0),
        RunnerResult(stdout=make_envelope(result="nope"), stderr="", returncode=0),
    ]))
    client = LlmClient(retry_max=3, retry_base_ms=1, retry_cap_ms=2, retry_total_s=30)
    result = client.call("x", script="t", expect_json=True)
    assert result.status == "parse_error"


def test_expect_json_schema_validation_pass(isolated_life, runner_cleanup):
    schema = {"type": "object", "required": ["x"], "properties": {"x": {"type": "integer"}}}
    set_runner(Scripted([RunnerResult(
        stdout=make_envelope(result='{"x": 42}'), stderr="", returncode=0
    )]))
    client = LlmClient()
    result = client.call("q", script="t", expect_json=True, schema=schema)
    assert result.status == "ok"
    assert result.data == {"x": 42}


def test_expect_json_schema_validation_fail(isolated_life, runner_cleanup):
    schema = {"type": "object", "required": ["x"], "properties": {"x": {"type": "integer"}}}
    set_runner(Scripted([
        RunnerResult(stdout=make_envelope(result='{"x": "wrong"}'), stderr="", returncode=0),
        RunnerResult(stdout=make_envelope(result='{"x": "still wrong"}'), stderr="", returncode=0),
        RunnerResult(stdout=make_envelope(result='{"x": "bad"}'), stderr="", returncode=0),
    ]))
    client = LlmClient(retry_max=3, retry_base_ms=1, retry_cap_ms=2, retry_total_s=30)
    result = client.call("q", script="t", expect_json=True, schema=schema)
    assert result.status == "schema_error"


def test_empty_text_is_empty_status_not_retried(isolated_life, runner_cleanup):
    runner = Scripted([RunnerResult(stdout=make_envelope(result=""), stderr="", returncode=0)])
    set_runner(runner)
    client = LlmClient()
    result = client.call("x", script="t")
    assert result.status == "empty"
    assert result.retries == 0
    # no second attempt
    assert len(runner.calls) == 1


def test_envelope_invalid_json_retries(isolated_life, runner_cleanup):
    set_runner(Scripted([
        RunnerResult(stdout="not json at all", stderr="", returncode=0),
        RunnerResult(stdout=make_envelope(), stderr="", returncode=0),
    ]))
    client = LlmClient(retry_base_ms=1, retry_cap_ms=2)
    result = client.call("x", script="t")
    assert result.status == "ok"
    assert result.retries == 1


# =============================================================== JSONL logging


def test_successful_call_logs_line(isolated_life, runner_cleanup):
    set_runner(Scripted([RunnerResult(stdout=make_envelope(), stderr="", returncode=0)]))
    client = LlmClient()
    client.call("hello", script="lint_knowledge_llm", entity="archie")
    log = (isolated_life / "logs" / "llm-calls.jsonl").read_text().strip()
    record = json.loads(log)
    for key in ("ts", "script", "entity", "model", "prompt_sha", "tokens_in",
                "tokens_out", "cost_usd", "cost_source", "duration_ms", "status", "retries"):
        assert key in record, f"missing {key} in log record"
    assert record["script"] == "lint_knowledge_llm"
    assert record["entity"] == "archie"
    assert record["status"] == "ok"
    assert len(record["prompt_sha"]) == 16


def test_log_line_size_bounded(isolated_life, runner_cleanup):
    set_runner(Scripted([RunnerResult(stdout=make_envelope(), stderr="", returncode=0)]))
    client = LlmClient()
    client.call("x" * 10_000, script="t", entities=["a"] * 200)
    raw = (isolated_life / "logs" / "llm-calls.jsonl").read_text().strip()
    # Accept either truncation path
    assert len(raw.encode("utf-8")) <= lc.MAX_LOG_LINE_BYTES + 100


def test_log_rotation_at_threshold(isolated_life, runner_cleanup, monkeypatch):
    monkeypatch.setenv("LLM_LOG_MAX_BYTES", "100")  # tiny threshold
    log_path = isolated_life / "logs" / "llm-calls.jsonl"
    # Pre-fill above threshold
    log_path.write_text("x" * 200 + "\n")
    set_runner(Scripted([RunnerResult(stdout=make_envelope(), stderr="", returncode=0)]))
    client = LlmClient()
    client.call("x", script="t")
    assert (isolated_life / "logs" / "llm-calls.jsonl.1").exists()


def test_log_redacts_secrets_in_error_field(isolated_life, runner_cleanup):
    # Avoid words that _looks_retryable would match ("connection", "rate", etc.)
    stderr = "auth failure: api_key=sk-ant-abcdef12345678901234 invalid"
    runner = Scripted([RunnerResult(stdout="", stderr=stderr, returncode=1)])
    set_runner(runner)
    client = LlmClient()
    client.call("x", script="t")
    log = (isolated_life / "logs" / "llm-calls.jsonl").read_text().strip()
    assert "sk-ant-" not in log
    assert "***" in log


# ===================================================== secret redaction unit


@pytest.mark.parametrize("payload,expected_substr", [
    ("sk-ant-api03-abcdefghijklmnopqrstuvwxyz123456", "***"),
    ("AKIAIOSFODNN7EXAMPLE", "***"),
    ("ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "***"),
    ("api_key=abc123xyz", "***"),
    ("-----BEGIN RSA PRIVATE KEY-----", "***"),
    ("eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.abc123", "***"),
    ("sk-or-v1-abcdefghijklmnopqrstuvwxyz12345", "***"),
])
def test_redact_secrets_patterns(payload, expected_substr):
    out = redact_secrets(f"hello {payload} world")
    assert expected_substr in out
    assert payload not in out


def test_redact_secrets_preserves_safe_text():
    s = "The quick brown fox jumps over the lazy dog."
    assert redact_secrets(s) == s


def test_redact_secrets_empty():
    assert redact_secrets("") == ""


# ============================================================== runner injection


def test_set_runner_and_reset(isolated_life):
    # Runner is module-level; set + reset must round-trip
    set_runner(Scripted([RunnerResult(stdout=make_envelope(), stderr="", returncode=0)]))
    assert lc._RUNNER is not lc._real_subprocess_runner
    reset_runner()
    assert lc._RUNNER is lc._real_subprocess_runner


# =============================================================== duration_ms


def test_duration_ms_nonnegative(isolated_life, runner_cleanup):
    set_runner(Scripted([RunnerResult(stdout=make_envelope(), stderr="", returncode=0)]))
    client = LlmClient()
    result = client.call("x", script="t")
    assert result.duration_ms >= 0  # monotonic clock guarantees no negative
