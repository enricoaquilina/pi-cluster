#!/usr/bin/env python3
"""
Unified episodic event log for ~/life/ cross-platform activity tracking.

Every write to ~/life/ (facts, entities, daily notes, sessions) logs an event here.
Events are tagged with the originating platform (claude-code, maxwell, hermes, etc.)
so each platform can see what others did.

Files: ~/life/logs/episodic-YYYY-MM.jsonl (monthly rotation, append-only)

Usage as library:
    from episodic import log_event, recent_activity

    log_event("claude-code", "fact_added", entity="pi-cluster",
              detail="Migrated gateway to gemini-2.5-pro", importance=7)

    events = recent_activity(hours=24, platforms=["maxwell"])
"""
import fcntl
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
LOGS_DIR = LIFE_DIR / "logs"

VALID_PLATFORMS = {
    "claude-code", "maxwell", "cursor", "windsurf", "cline",
    "aider", "codex", "hermes", "opencode", "mcp-client",
    "nightly-consolidate", "heartbeat-agent",
}

VALID_EVENTS = {
    "fact_added", "fact_reinforced", "entity_created", "daily_note_appended",
    "decision_made", "session_ended", "dispatch_ingested", "relationship_added",
    "skill_created", "tacit_knowledge_appended", "candidate_staged",
    "candidate_graduated", "candidate_rejected",
}

FLOCK_TIMEOUT = 2.0
FLOCK_POLL_INTERVAL = 0.05


def _log_path(dt: date | None = None) -> Path:
    """Monthly-rotated log file path."""
    dt = dt or date.today()
    return LOGS_DIR / f"episodic-{dt.strftime('%Y-%m')}.jsonl"


def log_event(
    platform: str,
    event: str,
    *,
    entity: str = "",
    detail: str = "",
    importance: int = 5,
    source_file: str = "",
    run_id: str = "",
) -> bool:
    """Append an event to the episodic log. Returns True on success.

    Fails silently on lock timeout or write error — callers should
    wrap in try/except and not let logging failures block real work.
    """
    if platform not in VALID_PLATFORMS:
        print(f"[episodic] WARNING: unknown platform '{platform}'", file=sys.stderr)
    if event not in VALID_EVENTS:
        print(f"[episodic] WARNING: unknown event '{event}'", file=sys.stderr)

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "platform": platform,
        "run_id": run_id,
        "event": event,
        "entity": entity,
        "detail": detail[:500],
        "importance": max(0, min(10, importance)),
        "source_file": source_file,
    }

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    path = _log_path()

    fd = None
    try:
        fd = open(path, "a", encoding="utf-8")
        deadline = time.monotonic() + FLOCK_TIMEOUT
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    print("[episodic] WARNING: flock timeout", file=sys.stderr)
                    return False
                time.sleep(FLOCK_POLL_INTERVAL)

        fd.write(json.dumps(entry, ensure_ascii=False) + "\n")
        fd.flush()
        return True

    except (OSError, IOError) as e:
        print(f"[episodic] WARNING: log_event failed: {e}", file=sys.stderr)
        return False

    finally:
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
                fd.close()
            except OSError:
                pass


def recent_activity(
    hours: int = 24,
    platforms: list[str] | None = None,
    limit: int = 20,
) -> list[dict]:
    """Read recent events from current + previous month's log files.

    Returns newest-first, filtered by time window and optional platform list.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    today = date.today()

    paths = []
    paths.append(_log_path(today))
    first_of_month = today.replace(day=1)
    prev_month = first_of_month - timedelta(days=1)
    prev_path = _log_path(prev_month)
    if prev_path != paths[0]:
        paths.append(prev_path)

    entries = []
    for path in paths:
        if not path.exists():
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts_str = entry.get("ts", "")
                    try:
                        ts = datetime.fromisoformat(ts_str)
                    except (ValueError, TypeError):
                        continue
                    if ts < cutoff:
                        continue
                    if platforms and entry.get("platform") not in platforms:
                        continue
                    entries.append(entry)
        except OSError:
            continue

    entries.sort(key=lambda e: e.get("ts", ""), reverse=True)
    return entries[:limit]


def platform_summary(hours: int = 24) -> dict[str, int]:
    """Count events per platform in the given time window."""
    events = recent_activity(hours=hours, limit=10000)
    counts: dict[str, int] = {}
    for e in events:
        p = e.get("platform", "unknown")
        counts[p] = counts.get(p, 0) + 1
    return counts
