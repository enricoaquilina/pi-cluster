#!/usr/bin/env python3
"""
Extract session digests from Claude Code transcripts.

Two modes:
  Hook mode:  Receives Stop hook JSON on stdin with transcript_path.
  Scan mode:  --scan flag scans ~/.claude/projects/ for unprocessed transcripts.

Output: Appends JSON lines to ~/life/Daily/YYYY/MM/sessions-digest-YYYY-MM-DD.jsonl

Usage:
    # Hook mode (called by cc_stop_hook.sh):
    echo '{"session_id":"...","transcript_path":"..."}' | python3 cc_session_digest.py

    # Scan mode (called by mini-consolidate.sh / timer):
    python3 cc_session_digest.py --scan
"""
import fcntl
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

try:
    from episodic import log_event as _log_event
except ImportError:
    _log_event = None

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
CLAUDE_PROJECTS = Path(os.environ.get(
    "CLAUDE_PROJECTS_DIR",
    Path.home() / ".claude" / "projects" / "-home-enrico",
))
MIN_MESSAGES = 5  # Skip trivial sessions
MAX_ASSISTANT_SCAN = 100  # Scan last N assistant messages for decisions
MAX_DECISIONS = 3

DECISION_PATTERNS = [
    re.compile(r"(?i)\b(decided to|decision:|chose|choosing|switched to|replaced .+ with|going with|opted for)\b"),
    re.compile(r"(?i)\b(keep .+ (?:instead|over)|drop(?:ping)? .+ in favor)\b"),
    re.compile(r"(?i)\b(deferred|defer(?:ring)?|built .+ instead|use .+ instead of)\b"),
    re.compile(r"(?i)\b(moved to|migrated? to|upgraded? to|reverted? to)\b"),
]

# Pattern to extract decisions from daily note "## Decisions Made" section
DECISION_SECTION_PATTERN = re.compile(
    r"(?m)^##\s*Decisions?\s*(?:Made)?\s*\n((?:[-*]\s+.+\n?)+)"
)

# Paths to exclude from key files
KEY_FILE_EXCLUDES = [".claude/plans/", "/life/logs/", "/tmp/"]


def _classify_session(tool_counts: dict) -> str:
    """Classify session type based on tool usage profile."""
    total = sum(tool_counts.values())
    if not total:
        return "chat"
    edit_ratio = (tool_counts.get("Edit", 0) + tool_counts.get("Write", 0)) / total
    read_ratio = (tool_counts.get("Read", 0) + tool_counts.get("Grep", 0) + tool_counts.get("Glob", 0)) / total
    bash_ratio = tool_counts.get("Bash", 0) / total
    if edit_ratio > 0.3:
        return "coding"
    if read_ratio > 0.4:
        return "research"
    if bash_ratio > 0.4:
        return "ops"
    if total > 0 and tool_counts.get("Agent", 0) / total > 0.2:
        return "research"
    return "mixed"


def _daily_note_path() -> Path:
    """Get today's daily note path."""
    today = str(date.today())
    parts = today.split("-")
    return LIFE_DIR / "Daily" / parts[0] / parts[1] / f"{today}.md"


def _extract_decisions(assistant_texts: list[str], daily_note: Path | None = None) -> list[str]:
    """Extract decisions from daily note section AND assistant message text."""
    decisions: list[str] = []

    # 1. Try daily note "Decisions Made" section first (most reliable)
    if daily_note is None:
        daily_note = _daily_note_path()
    if daily_note.exists():
        try:
            text = daily_note.read_text(encoding="utf-8")
            match = DECISION_SECTION_PATTERN.search(text)
            if match:
                for line in match.group(1).strip().splitlines():
                    line = line.lstrip("-* ").strip()
                    if line and len(line) > 10:
                        decisions.append(line[:100])
                        if len(decisions) >= MAX_DECISIONS:
                            return decisions
        except OSError:
            pass

    # 2. Fall back to regex on assistant text
    for text in assistant_texts[-MAX_ASSISTANT_SCAN:]:
        for pattern in DECISION_PATTERNS:
            for match in pattern.finditer(text):
                start = text.rfind(".", 0, match.start())
                start = start + 1 if start >= 0 else max(0, match.start() - 50)
                end = text.find(".", match.end())
                end = end + 1 if end >= 0 else min(len(text), match.end() + 50)
                sentence = text[start:end].strip()
                if sentence and len(sentence) > 10:
                    decisions.append(sentence[:100])
                    if len(decisions) >= MAX_DECISIONS:
                        return decisions
                    break
    return decisions


def _top_files(files_touched: set, n: int = 3) -> list[str]:
    """Return top N files, excluding logs/plans/tmp paths. Shortest basename wins for display."""
    filtered = [f for f in files_touched if not any(exc in f for exc in KEY_FILE_EXCLUDES)]
    # Sort by path, return basenames
    return [Path(f).name for f in sorted(filtered)[:n]]


def _digest_path(session_date: date) -> Path:
    """Return path to the sessions-digest JSONL for a given date."""
    return LIFE_DIR / "Daily" / str(session_date.year) / f"{session_date.month:02d}" / f"sessions-digest-{session_date}.jsonl"


def _extract_session_id(transcript_path: Path) -> str:
    """Extract session ID from transcript filename (UUID.jsonl)."""
    return transcript_path.stem


def _process_transcript(transcript_path: Path) -> dict | None:
    """Parse a transcript JSONL and return a digest dict, or None if trivial."""
    if not transcript_path.is_file():
        return None

    msg_count = 0
    tool_counts: dict[str, int] = {}
    files_touched: set[str] = set()
    first_user_text = ""
    session_ts = ""
    assistant_texts: list[str] = []  # For decision detection

    try:
        with transcript_path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                entry_type = entry.get("type", "")
                if entry_type not in ("user", "assistant"):
                    continue

                msg_count += 1
                msg = entry.get("message", {})
                if not isinstance(msg, dict):
                    continue

                # Capture session start time from first user message
                if not session_ts and entry_type == "user":
                    session_ts = entry.get("timestamp", "")

                content = msg.get("content", [])

                # Handle content as string (some edge cases)
                if isinstance(content, str):
                    if entry_type == "user" and not first_user_text:
                        first_user_text = content[:200]
                    elif entry_type == "assistant":
                        assistant_texts.append(content)
                    continue

                if not isinstance(content, list):
                    continue

                for item in content:
                    if not isinstance(item, dict):
                        continue

                    # Extract first user message text
                    if entry_type == "user" and not first_user_text and item.get("type") == "text":
                        first_user_text = (item.get("text", "") or "")[:200]

                    # Collect assistant text for decision detection
                    if entry_type == "assistant" and item.get("type") == "text":
                        text = item.get("text", "")
                        if text:
                            assistant_texts.append(text)

                    # Count tool calls and extract file paths
                    if item.get("type") == "tool_use":
                        name = item.get("name", "unknown")
                        tool_counts[name] = tool_counts.get(name, 0) + 1

                        inp = item.get("input")
                        if isinstance(inp, dict):
                            for key in ("file_path", "path"):
                                val = inp.get(key)
                                if val and isinstance(val, str) and "/" in val:
                                    files_touched.add(val)
    except OSError:
        return None

    if msg_count < MIN_MESSAGES:
        return None

    total_tools = sum(tool_counts.values())
    topic = first_user_text.replace("\n", " ").strip() or "No topic captured"

    # Session type classification
    session_type = _classify_session(tool_counts)

    # Decision detection from assistant messages
    decisions = _extract_decisions(assistant_texts)

    # Key files (top 3, excluding logs/plans)
    key_files = _top_files(files_touched)

    # Build rich summary
    parts = [f"[{session_type}]"]
    parts.append(topic[:80])
    if decisions:
        parts.append("Decisions: " + "; ".join(decisions[:MAX_DECISIONS]))
    parts.append(f"{total_tools} tools, {msg_count} msgs.")
    if key_files:
        parts.append("Key files: " + ", ".join(key_files))
    summary = " ".join(parts)

    # Determine date from session timestamp
    session_date = date.today()
    if session_ts:
        try:
            session_date = datetime.fromisoformat(session_ts).date()
        except (ValueError, TypeError):
            pass

    return {
        "session_id": _extract_session_id(transcript_path),
        "ts": session_ts or datetime.now().isoformat(),
        "cwd": "",  # Not available from transcript alone
        "summary": summary,
        "session_type": session_type,
        "decisions": decisions,
        "files_touched": sorted(files_touched),
        "tool_counts": dict(sorted(tool_counts.items(), key=lambda x: -x[1])),
        "msg_count": msg_count,
        "_date": str(session_date),
    }


def _existing_session_ids(digest_path: Path) -> set[str]:
    """Read already-processed session IDs from the digest JSONL."""
    ids: set[str] = set()
    if not digest_path.is_file():
        return ids
    try:
        for line in digest_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                sid = d.get("session_id", "")
                if sid:
                    ids.add(sid)
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return ids


def _append_digest(digest: dict, digest_path: Path) -> None:
    """Append a single digest line to the JSONL file with flock."""
    digest_path.parent.mkdir(parents=True, exist_ok=True)
    # Remove internal _date field before writing
    output = {k: v for k, v in digest.items() if not k.startswith("_")}
    line = json.dumps(output, ensure_ascii=False) + "\n"

    fd = os.open(str(digest_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    try:
        # Wait up to 5 seconds for lock
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, line.encode("utf-8"))
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    if _log_event:
        try:
            _log_event("claude-code", "session_ended",
                       detail=digest.get("summary", "")[:200], importance=4,
                       run_id=digest.get("session_id", ""))
        except Exception:
            pass

    # Index into FTS5 for cross-session search (non-critical)
    try:
        import subprocess as _sp
        _sp.run(
            [sys.executable, str(LIFE_DIR / "scripts" / "session_search.py"),
             "--index-stdin"],
            input=json.dumps(output), text=True, timeout=5,
            capture_output=True,
        )
    except Exception:
        pass


def _run_hook_mode() -> bool:
    """Process a single transcript from Stop hook stdin. Returns True if handled."""
    try:
        raw = sys.stdin.read()
    except Exception:
        return False

    if not raw or not raw.strip():
        return False

    try:
        hook_data = json.loads(raw)
    except json.JSONDecodeError:
        return False

    transcript_path = hook_data.get("transcript_path", "")
    if not transcript_path:
        return False

    tp = Path(transcript_path)
    digest = _process_transcript(tp)
    if digest is None:
        return True  # Handled (trivial session), just nothing to write

    # Enrich with hook data
    digest["cwd"] = hook_data.get("cwd", "")
    digest["session_id"] = hook_data.get("session_id", digest["session_id"])

    session_date = date.fromisoformat(digest["_date"])
    dp = _digest_path(session_date)

    # Check if already processed
    if digest["session_id"] in _existing_session_ids(dp):
        return True

    _append_digest(digest, dp)
    return True


def _run_scan_mode() -> None:
    """Scan for unprocessed transcripts and process them.

    Digests are filed by the transcript's own date (not today's date),
    so a missed session from yesterday lands in yesterday's digest file.
    """
    if not CLAUDE_PROJECTS.is_dir():
        return

    today = date.today()
    lock_path = _digest_path(today)

    # Use today's digest as the lock file to prevent race with hook mode
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(str(lock_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # Another process holds the lock — skip this scan
            return

        # Find recent transcripts across all project subdirectories
        candidate_dates: set[date] = {today}
        candidate_transcripts = []
        projects_root = CLAUDE_PROJECTS.parent
        scan_dirs = [CLAUDE_PROJECTS]
        if projects_root.is_dir():
            scan_dirs = [d for d in projects_root.iterdir() if d.is_dir()]
        for scan_dir in scan_dirs:
            for f in scan_dir.glob("*.jsonl"):
                if f.name.startswith("."):
                    continue
                try:
                    mtime_date = datetime.fromtimestamp(f.stat().st_mtime).date()
                except OSError:
                    continue
                if (today - mtime_date).days > 1:
                    continue
                candidate_transcripts.append(f)

        # Process candidates to learn their target dates
        new_digests = []
        for f in candidate_transcripts:
            session_id = _extract_session_id(f)
            digest = _process_transcript(f)
            if digest is not None:
                target = date.fromisoformat(digest.get("_date", str(today)))
                candidate_dates.add(target)
                new_digests.append((session_id, digest))

        # Build existing set from ALL candidate target dates (not just today/yesterday)
        existing: set[str] = set()
        for d in candidate_dates:
            existing.update(_existing_session_ids(_digest_path(d)))

        # Filter out already-processed and prevent intra-scan duplicates
        filtered = []
        for session_id, digest in new_digests:
            if session_id not in existing:
                filtered.append(digest)
                existing.add(session_id)
        new_digests = filtered

        # Group by transcript date and append to correct digest file
        by_date: dict[str, list[dict]] = {}
        for digest in new_digests:
            d = digest.get("_date", str(today))
            by_date.setdefault(d, []).append(digest)

        for date_str, digests in by_date.items():
            target = _digest_path(date.fromisoformat(date_str))
            if target == lock_path:
                # We already hold the lock on this file — write directly
                for digest in digests:
                    output = {k: v for k, v in digest.items() if not k.startswith("_")}
                    line = json.dumps(output, ensure_ascii=False) + "\n"
                    os.write(lock_fd, line.encode("utf-8"))
            else:
                # Different file — use _append_digest which acquires its own lock
                for digest in digests:
                    _append_digest(digest, target)

    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def main():
    try:
        if "--scan" in sys.argv:
            _run_scan_mode()
        elif not sys.stdin.isatty():
            # Try hook mode, fall through to scan if stdin is empty/invalid
            if not _run_hook_mode():
                _run_scan_mode()
        else:
            _run_scan_mode()
    except Exception:
        pass  # Never fail — exit 0 always

    sys.exit(0)


if __name__ == "__main__":
    main()
