"""Structured dispatch events log.

One JSONL line per dispatch (success, failure, degraded, kill-switch,
guard-hit). Turns the dispatch path from opaque into queryable. Consumers:

- Operators: ``tail -f /var/log/maxwell/events.jsonl | jq``
- SLIs: daily cron that queries ``error_class IS NULL`` / ``guard_hit = false``
- Future ``maxwellctl`` CLI

Schema (REQUIRED_FIELDS is the contract; callers may add extras):

- ``ts``              ISO-8601 UTC timestamp (auto-filled if absent)
- ``persona``         persona name as supplied by the caller
- ``status``          "success" | "error" | "degraded" | "kill_switch"
- ``degraded_level``  L0-L5 from dispatch_degradation (always set)
- ``llm_ms``          round-trip time for the LLM call (null on degradation)
- ``prompt_sha256``   first 16 hex chars of sha256(user prompt), privacy-safe
- ``prompt_bytes``    byte length of the user prompt
- ``guard_hit``       true if outbound_guard replaced the response
- ``error_class``     exception class name, or null
- ``sender``          optional per-channel identifier (hashed if sensitive)

Configuration: ``MAXWELL_EVENTS_LOG`` env var chooses the destination.
Default: ``/var/log/maxwell/events.jsonl`` inside the container.

If the destination is unwritable (read-only FS, missing parent, permission
denied), append() logs a warning and returns False — dispatch is never
taken down by a broken log destination.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger("mission-control.events_log")

DEFAULT_EVENTS_LOG = "/var/log/maxwell/events.jsonl"

REQUIRED_FIELDS = (
    "ts",
    "persona",
    "status",
    "degraded_level",
    "llm_ms",
    "prompt_sha256",
    "prompt_bytes",
    "guard_hit",
    "error_class",
)


def resolve_log_path() -> str:
    """Return the current events-log destination path."""
    return os.environ.get("MAXWELL_EVENTS_LOG", DEFAULT_EVENTS_LOG)


class EventLogger:
    """Append-only JSONL logger.

    Intentionally simple: open-append-close per call. At 5 messages/day the
    fsync overhead is irrelevant, and this avoids file-handle-leak concerns
    across worker reloads. Thread-safety is inherited from the OS's append
    atomicity guarantee on POSIX (writes shorter than ``PIPE_BUF`` are
    atomic; one JSON line is well under that).
    """

    def append(self, event: Dict[str, Any]) -> bool:
        """Append one event. Returns True on success, False on silent failure."""
        payload = self._normalize(event)
        path = resolve_log_path()
        try:
            self._ensure_dirs(path)
            self._write_line(path, payload)
            return True
        except OSError as e:
            logger.warning("events_log: cannot write to %s (%s)", path, e)
            return False

    # ── Internals ────────────────────────────────────────────────────────────

    def _normalize(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Populate required fields with defaults if the caller omitted them."""
        out = dict(event)  # shallow copy so we don't mutate caller's dict
        if "ts" not in out or out["ts"] is None:
            out["ts"] = datetime.now(timezone.utc).isoformat()
        for field in REQUIRED_FIELDS:
            out.setdefault(field, None)
        return out

    def _ensure_dirs(self, path: str) -> None:
        parent = Path(path).parent
        if parent.exists():
            return
        parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(parent, 0o700)
        except OSError:
            # Best-effort permission tightening; some filesystems ignore chmod.
            pass

    def _write_line(self, path: str, payload: Dict[str, Any]) -> None:
        line = json.dumps(payload, default=str, separators=(",", ":"))
        p = Path(path)
        # Open O_APPEND so concurrent writers don't clobber each other.
        # Create with mode 0600 if file is new; tighten mode on existing file
        # to the same permissions.
        fd = os.open(
            str(p),
            os.O_WRONLY | os.O_APPEND | os.O_CREAT,
            0o600,
        )
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        # Defensive: enforce 0600 in case umask widened the file on creation.
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass


# Module-level singleton for convenience; tests can still instantiate EventLogger().
event_logger = EventLogger()
