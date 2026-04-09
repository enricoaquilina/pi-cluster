"""Phase 8.0.2 — LLM client wrapper around the ``claude`` CLI.

All LLM-calling scripts (8A lint, 8C rewrite, any future consumer) go through
this module. It provides:

* Subprocess invocation of ``claude -p --output-format json`` with usage/cost
  parsing from the envelope.
* Hand-rolled retry with decorrelated jitter (no `tenacity` dependency).
* Two-tier cost cap: per-run (process-local) and per-nightly (shared across
  processes via `fcntl.flock` on `llm-cost-window.json`).
* JSONL append-only logging of every call with secret redaction + rotation.
* ``expect_json=True`` path that strips ``` fences and validates against an
  optional ``jsonschema``.
* Test injection via ``set_runner(fn)``.

The honoring of ``LIFE_LLM_DISABLED`` is the *caller's* responsibility — see
``life_kill_switch.check_llm_kill_switch``. This module focuses on the
mechanics of an actual LLM call when one is authorized.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Optional

__all__ = [
    "LlmResult",
    "CostCapExceeded",
    "LlmClient",
    "call_haiku",
    "set_runner",
    "reset_runner",
]

# ----------------------------------------------------------------- constants

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_CLAUDE_BIN = os.environ.get("CLAUDE_BIN", str(Path.home() / ".local" / "bin" / "claude"))

# Fallback cost estimation when envelope lacks usage (per 1k tokens, USD).
# Haiku 4.5 pricing as of 2026-04. Adjust if upstream changes.
FALLBACK_COST_IN_PER_1K = 0.001
FALLBACK_COST_OUT_PER_1K = 0.005

# Log line size cap — keeps a single `write()` under Linux PIPE_BUF so
# concurrent appenders don't interleave.
MAX_LOG_LINE_BYTES = 4000

# Secret redaction — pre-log scrubber.
# Patterns are ordered from most specific to most generic.
SECRET_PATTERNS = [
    # Anthropic + OpenRouter keys (project-relevant)
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"sk-or-v1-[A-Za-z0-9_\-]{20,}"),
    # AWS
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"aws_secret_access_key\s*[:=]\s*[A-Za-z0-9/+=]{40}", re.I),
    # GitHub
    re.compile(r"ghp_[A-Za-z0-9]{36}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{82}"),
    # Slack
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    # Stripe
    re.compile(r"sk_live_[A-Za-z0-9]{24,}"),
    # JWT (header.body.sig)
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]+"),
    # Private key headers
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    # Generic key=value
    re.compile(
        r"(?i)(api[_\-]?key|bearer|token|password|secret)\s*[:=]\s*[^\s\"',}]+"
    ),
]


def redact_secrets(text: str) -> str:
    """Replace recognised secrets with ``***`` markers.

    Redaction is best-effort; the plan treats this as defense-in-depth. Callers
    should NOT rely on it to exfil-proof arbitrary content — the pre-send abort
    path is a separate defense (see plan v3 deferred P1 items).
    """
    if not text:
        return text
    out = text
    for pat in SECRET_PATTERNS:
        out = pat.sub("***", out)
    return out


# ----------------------------------------------------------------- exceptions


class CostCapExceeded(Exception):
    """Raised when a planned call would push past the per-run or per-nightly cap."""


# ----------------------------------------------------------------- datatypes


StatusLiteral = Literal[
    "ok",
    "parse_error",
    "schema_error",
    "empty",
    "cost_cap_exceeded",
    "timeout",
    "error",
]


@dataclass
class LlmResult:
    text: str
    data: Any = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    cost_source: Literal["actual", "estimated"] = "actual"
    duration_ms: int = 0
    retries: int = 0
    status: StatusLiteral = "ok"
    error: str = ""


# ------------------------------------------------------ subprocess runner API


@dataclass
class RunnerResult:
    """Minimal subset of ``subprocess.CompletedProcess`` we care about."""

    stdout: str
    stderr: str
    returncode: int


Runner = Callable[[list[str], float], RunnerResult]


def _real_subprocess_runner(argv: list[str], timeout_s: float) -> RunnerResult:
    try:
        cp = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        raise _TimeoutMarker(str(exc)) from exc
    except FileNotFoundError as exc:
        raise _NotFoundMarker(str(exc)) from exc
    return RunnerResult(stdout=cp.stdout or "", stderr=cp.stderr or "", returncode=cp.returncode)


class _TimeoutMarker(Exception):
    pass


class _NotFoundMarker(Exception):
    pass


_RUNNER: Runner = _real_subprocess_runner


def set_runner(runner: Runner) -> None:
    """Install a test-supplied runner. Not thread-safe; use in tests only."""
    global _RUNNER
    _RUNNER = runner


def reset_runner() -> None:
    global _RUNNER
    _RUNNER = _real_subprocess_runner


# -------------------------------------------------------------- cost ledger


def _nightly_ledger_path() -> Path:
    life_dir = Path(os.environ.get("LIFE_DIR", str(Path.home() / "life")))
    return life_dir / "logs" / "llm-cost-window.json"


def _nightly_run_id() -> str:
    return os.environ.get("NIGHTLY_RUN_ID", "")


def _read_ledger(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        # Corrupt ledger: fail-reduced (per plan v3 deferred item). Quarantine
        # the file so next run starts clean, but refuse to zero out silently.
        quarantine = path.with_name(path.name + f".corrupt.{int(time.time())}")
        try:
            path.rename(quarantine)
        except OSError:
            pass
        return {"_corrupt": True}


def _write_ledger(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(data, sort_keys=True))
    os.replace(tmp, path)


# ------------------------------------------------------------- JSONL logger


def _llm_log_path() -> Path:
    life_dir = Path(os.environ.get("LIFE_DIR", str(Path.home() / "life")))
    return life_dir / "logs" / "llm-calls.jsonl"


def _rotate_if_needed(path: Path, max_bytes: int) -> None:
    try:
        if path.exists() and path.stat().st_size > max_bytes:
            rotated = path.with_name(path.name + ".1")
            try:
                if rotated.exists():
                    rotated.unlink()
                path.rename(rotated)
            except OSError:
                pass
    except OSError:
        pass


def _truncate_str(value: Any, max_len: int) -> Any:
    if isinstance(value, str) and len(value) > max_len:
        return value[:max_len] + "…"
    return value


def _emit_log_line(record: dict) -> bool:
    """Append a redacted, bounded JSONL record. Returns True on success."""
    # Redact + truncate string fields
    safe: dict[str, Any] = {}
    for k, v in record.items():
        if isinstance(v, str):
            safe[k] = redact_secrets(_truncate_str(v, 500))
        else:
            safe[k] = v
    line = json.dumps(safe, sort_keys=True, ensure_ascii=False)
    if len(line.encode("utf-8")) > MAX_LOG_LINE_BYTES:
        # Aggressive re-truncation: keep essentials
        essentials = {
            k: safe[k]
            for k in ("ts", "script", "entity", "status", "cost_usd", "duration_ms")
            if k in safe
        }
        essentials["_truncated"] = True
        line = json.dumps(essentials, sort_keys=True)
    path = _llm_log_path()
    max_bytes = int(os.environ.get("LLM_LOG_MAX_BYTES", str(5 * 1024 * 1024)))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed(path, max_bytes)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        return True
    except OSError as exc:
        # Fail-open: logging failure never aborts the LLM call.
        print(f"[llm_client] log-degraded: {exc}", file=sys.stderr)
        return False


# ----------------------------------------------------------------- utilities


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _estimate_cost(in_tokens: int, out_tokens: int) -> float:
    return (in_tokens / 1000) * FALLBACK_COST_IN_PER_1K + (out_tokens / 1000) * FALLBACK_COST_OUT_PER_1K


def _strip_json_fences(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        # drop the opening fence line
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        # drop trailing fence if present
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def _git_sha() -> str:
    """Cheap, cached canonical tree git SHA for log correlation."""
    if hasattr(_git_sha, "_cached"):
        return _git_sha._cached  # type: ignore[attr-defined]
    try:
        canonical = Path(__file__).resolve().parent
        cp = subprocess.run(
            ["git", "-C", str(canonical), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        sha = (cp.stdout or "").strip()
    except Exception:
        sha = ""
    _git_sha._cached = sha  # type: ignore[attr-defined]
    return sha


# --------------------------------------------------------------- main client


@dataclass
class LlmClient:
    model: str = DEFAULT_MODEL
    claude_bin: str = DEFAULT_CLAUDE_BIN
    max_cost_per_run_usd: float = float(os.environ.get("MAX_LLM_COST_USD_PER_RUN", "0.50"))
    max_cost_per_nightly_usd: float = float(os.environ.get("MAX_LLM_COST_USD_PER_NIGHTLY", "2.00"))
    retry_max: int = int(os.environ.get("LLM_RETRY_MAX", "3"))
    retry_base_ms: int = int(os.environ.get("LLM_RETRY_BASE_MS", "500"))
    retry_cap_ms: int = int(os.environ.get("LLM_RETRY_MAX_MS", "8000"))
    retry_total_s: float = float(os.environ.get("LLM_RETRY_TOTAL_S", "30"))
    _run_cost_usd: float = field(default=0.0, init=False)

    def call(
        self,
        prompt: str,
        *,
        script: str,
        entity: Optional[str] = None,
        entities: Optional[list[str]] = None,
        group_id: Optional[str] = None,
        expect_json: bool = False,
        schema: Optional[dict] = None,
        timeout_s: float = 60.0,
    ) -> LlmResult:
        """Execute an LLM call with retries, cost cap, and logging."""
        started_monotonic = time.monotonic_ns()
        # Pre-call cost estimate for cap check
        est_in = _estimate_tokens(prompt)
        est_out = 1024  # conservative upper bound
        est_cost = _estimate_cost(est_in, est_out)

        if self._run_cost_usd + est_cost > self.max_cost_per_run_usd:
            self._log(
                script=script, entity=entity, entities=entities, group_id=group_id,
                prompt=prompt, status="cost_cap_exceeded",
                tokens_in=0, tokens_out=0, cost_usd=0.0,
                cost_source="estimated", duration_ms=0, retries=0,
                error=f"per-run cap {self.max_cost_per_run_usd} would be exceeded",
            )
            raise CostCapExceeded(
                f"per-run cap {self.max_cost_per_run_usd} would be exceeded by estimated ${est_cost:.4f}"
            )

        # Nightly cap — only enforced when NIGHTLY_RUN_ID is set
        self._check_nightly_cap(est_cost)

        argv = [self.claude_bin, "-p", prompt, "--output-format", "json", "--model", self.model]
        attempts = 0
        last_error = ""
        wall_start = time.monotonic()
        prev_sleep_ms = self.retry_base_ms

        while True:
            attempts += 1
            try:
                rr = _RUNNER(argv, timeout_s)
            except _TimeoutMarker as exc:
                last_error = f"timeout: {exc}"
                if not self._should_retry(attempts, wall_start):
                    return self._finalize_error(
                        "timeout", last_error, script, entity, entities,
                        group_id, prompt, started_monotonic, attempts - 1,
                    )
                prev_sleep_ms = self._sleep(prev_sleep_ms)
                continue
            except _NotFoundMarker as exc:
                return self._finalize_error(
                    "error", f"claude CLI not found: {exc}", script, entity,
                    entities, group_id, prompt, started_monotonic, attempts - 1,
                )

            if rr.returncode != 0:
                last_error = f"exit {rr.returncode}: {rr.stderr[:200]}"
                if self._looks_retryable(rr.stderr) and self._should_retry(attempts, wall_start):
                    prev_sleep_ms = self._sleep(prev_sleep_ms)
                    continue
                return self._finalize_error(
                    "error", last_error, script, entity, entities, group_id,
                    prompt, started_monotonic, attempts - 1,
                )

            # Parse envelope
            try:
                envelope = json.loads(rr.stdout)
            except json.JSONDecodeError as exc:
                last_error = f"envelope-parse: {exc}"
                if self._should_retry(attempts, wall_start):
                    prev_sleep_ms = self._sleep(prev_sleep_ms)
                    continue
                return self._finalize_error(
                    "parse_error", last_error, script, entity, entities,
                    group_id, prompt, started_monotonic, attempts - 1,
                )

            text = (envelope.get("result") or "").strip()
            usage = envelope.get("usage") or {}
            tokens_in = int(usage.get("input_tokens") or 0)
            tokens_out = int(usage.get("output_tokens") or 0)
            cost_usd = float(envelope.get("total_cost_usd") or 0.0)
            cost_source: Literal["actual", "estimated"] = "actual"
            if tokens_in == 0 and tokens_out == 0:
                tokens_in = _estimate_tokens(prompt)
                tokens_out = _estimate_tokens(text)
                cost_source = "estimated"
            if cost_usd == 0.0 and cost_source == "estimated":
                cost_usd = _estimate_cost(tokens_in, tokens_out)

            # Account for cost under both caps
            self._run_cost_usd += cost_usd
            self._add_nightly_cost(cost_usd)

            duration_ms = (time.monotonic_ns() - started_monotonic) // 1_000_000

            # Empty output is not retryable — model said nothing
            if not text:
                self._log(
                    script=script, entity=entity, entities=entities, group_id=group_id,
                    prompt=prompt, status="empty", tokens_in=tokens_in,
                    tokens_out=tokens_out, cost_usd=cost_usd, cost_source=cost_source,
                    duration_ms=duration_ms, retries=attempts - 1,
                )
                return LlmResult(
                    text="", data=None, tokens_in=tokens_in, tokens_out=tokens_out,
                    cost_usd=cost_usd, cost_source=cost_source,
                    duration_ms=int(duration_ms), retries=attempts - 1, status="empty",
                )

            # expect_json handling
            data: Any = None
            status: StatusLiteral = "ok"
            if expect_json:
                try:
                    data = json.loads(_strip_json_fences(text))
                except json.JSONDecodeError as exc:
                    last_error = f"json: {exc}"
                    if self._should_retry(attempts, wall_start):
                        prev_sleep_ms = self._sleep(prev_sleep_ms)
                        continue
                    status = "parse_error"
                if status == "ok" and schema is not None:
                    try:
                        import jsonschema
                        jsonschema.validate(data, schema)
                    except Exception as exc:  # jsonschema.ValidationError subclass of Exception
                        last_error = f"schema: {exc}"
                        if self._should_retry(attempts, wall_start):
                            prev_sleep_ms = self._sleep(prev_sleep_ms)
                            continue
                        status = "schema_error"

            self._log(
                script=script, entity=entity, entities=entities, group_id=group_id,
                prompt=prompt, status=status, tokens_in=tokens_in,
                tokens_out=tokens_out, cost_usd=cost_usd, cost_source=cost_source,
                duration_ms=duration_ms, retries=attempts - 1,
                error=last_error if status != "ok" else "",
            )
            return LlmResult(
                text=text, data=data, tokens_in=tokens_in, tokens_out=tokens_out,
                cost_usd=cost_usd, cost_source=cost_source,
                duration_ms=int(duration_ms), retries=attempts - 1, status=status,
                error=last_error if status != "ok" else "",
            )

    # ------------------------------------------------------------ retry

    def _should_retry(self, attempts: int, wall_start: float) -> bool:
        if attempts >= self.retry_max:
            return False
        if time.monotonic() - wall_start >= self.retry_total_s:
            return False
        return True

    def _sleep(self, prev_ms: int) -> int:
        """Decorrelated jitter: sleep = uniform(base, prev*3), capped at max."""
        low = self.retry_base_ms
        high = min(self.retry_cap_ms, max(prev_ms * 3, low + 1))
        ms = random.uniform(low, high)
        time.sleep(ms / 1000)
        return int(ms)

    @staticmethod
    def _looks_retryable(stderr: str) -> bool:
        if not stderr:
            return False
        s = stderr.lower()
        return any(k in s for k in ("rate limit", "rate_limit", "overloaded", "529", "connection"))

    # --------------------------------------------------------- nightly cap

    def _check_nightly_cap(self, est_cost: float) -> None:
        run_id = _nightly_run_id()
        if not run_id:
            return  # manual invocation; only per-run cap applies
        path = _nightly_ledger_path()
        lock_path = path.with_name(path.name + ".lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+") as lockf:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
            try:
                ledger = _read_ledger(path)
                if ledger.get("_corrupt"):
                    # Fail-reduced: only per-run cap applies, but emit a pending marker
                    return
                current = float(ledger.get(run_id, 0.0))
                if current + est_cost > self.max_cost_per_nightly_usd:
                    raise CostCapExceeded(
                        f"per-nightly cap {self.max_cost_per_nightly_usd} would be exceeded "
                        f"(current={current:.4f}, est={est_cost:.4f})"
                    )
            finally:
                fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)

    def _add_nightly_cost(self, cost: float) -> None:
        run_id = _nightly_run_id()
        if not run_id:
            return
        path = _nightly_ledger_path()
        lock_path = path.with_name(path.name + ".lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+") as lockf:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
            try:
                ledger = _read_ledger(path)
                if ledger.get("_corrupt"):
                    ledger = {}
                ledger[run_id] = float(ledger.get(run_id, 0.0)) + cost
                _write_ledger(path, ledger)
            finally:
                fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)

    # --------------------------------------------------------- logging

    def _log(
        self,
        *,
        script: str,
        entity: Optional[str],
        entities: Optional[list[str]],
        group_id: Optional[str],
        prompt: str,
        status: str,
        tokens_in: int,
        tokens_out: int,
        cost_usd: float,
        cost_source: str,
        duration_ms: int,
        retries: int,
        error: str = "",
    ) -> None:
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "git_sha": _git_sha(),
            "script": script,
            "entity": entity,
            "entities": entities,
            "group_id": group_id,
            "model": self.model,
            "prompt_sha": hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16],
            "prompt_len": len(prompt),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": round(cost_usd, 6),
            "cost_source": cost_source,
            "duration_ms": int(duration_ms),
            "status": status,
            "retries": retries,
        }
        if error:
            record["error"] = error
        _emit_log_line(record)

    def _finalize_error(
        self,
        status: StatusLiteral,
        error: str,
        script: str,
        entity: Optional[str],
        entities: Optional[list[str]],
        group_id: Optional[str],
        prompt: str,
        started_monotonic: int,
        retries: int,
    ) -> LlmResult:
        duration_ms = (time.monotonic_ns() - started_monotonic) // 1_000_000
        self._log(
            script=script, entity=entity, entities=entities, group_id=group_id,
            prompt=prompt, status=status, tokens_in=0, tokens_out=0, cost_usd=0.0,
            cost_source="estimated", duration_ms=duration_ms, retries=retries,
            error=error,
        )
        return LlmResult(
            text="", data=None, tokens_in=0, tokens_out=0, cost_usd=0.0,
            cost_source="estimated", duration_ms=int(duration_ms), retries=retries,
            status=status, error=error,
        )


# --------------------------------------------- module-level convenience


_default_client: Optional[LlmClient] = None


def _get_default() -> LlmClient:
    global _default_client
    if _default_client is None:
        _default_client = LlmClient()
    return _default_client


def call_haiku(prompt: str, **kwargs) -> LlmResult:
    """Module-level convenience wrapper using a default LlmClient instance."""
    return _get_default().call(prompt, **kwargs)
