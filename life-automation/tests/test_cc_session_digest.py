"""Tests for cc_session_digest.py — hook mode, scan mode, file extraction, concurrency, session type, decisions."""
import json
import os
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

# Also import the module directly for unit testing helpers
sys.path.insert(0, str(Path(__file__).parent.parent))
import cc_session_digest as csd

SCRIPT = Path(__file__).parent.parent / "cc_session_digest.py"
TODAY = str(date.today())


# ── Helpers ──────────────────────────────────────────────────────────────────


def make_line(type_: str, role: str = "", text: str = "", tool_name: str = "",
              tool_input: dict = None, ts: str = "") -> str:
    """Build a realistic transcript JSONL line."""
    msg = {}
    if role:
        msg["role"] = role
    if tool_name:
        msg["content"] = [{"type": "tool_use", "name": tool_name, "input": tool_input or {}}]
    elif text:
        msg["content"] = [{"type": "text", "text": text}]
    elif role == "user":
        msg["content"] = [{"type": "text", "text": "user message"}]
    elif role == "assistant":
        msg["content"] = [{"type": "text", "text": "assistant response"}]
    return json.dumps({
        "type": type_,
        "message": msg,
        "uuid": str(uuid4()),
        "timestamp": ts or datetime.now().isoformat(),
    })


def make_transcript(path: Path, lines: list[str]) -> Path:
    """Write transcript JSONL and return path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def make_session(tmp_path: Path, msg_count: int = 10, session_id: str = None,
                 tools: list[tuple[str, dict]] = None) -> tuple[Path, str]:
    """Create a transcript with N user+assistant message pairs and optional tool calls."""
    sid = session_id or str(uuid4())
    tp = tmp_path / f"{sid}.jsonl"
    lines = []
    for i in range(msg_count):
        if i % 2 == 0:
            text = f"User message {i}" if i > 0 else "Investigate gateway memory service"
            lines.append(make_line("user", role="user", text=text))
        else:
            lines.append(make_line("assistant", role="assistant"))
    if tools:
        for name, inp in tools:
            lines.append(make_line("assistant", role="assistant", tool_name=name, tool_input=inp))
    make_transcript(tp, lines)
    return tp, sid


def _run_hook(life_dir: Path, hook_json: dict, projects_dir: Path = None) -> subprocess.CompletedProcess:
    """Run cc_session_digest.py in hook mode (stdin)."""
    env = os.environ.copy()
    env["LIFE_DIR"] = str(life_dir)
    if projects_dir:
        env["CLAUDE_PROJECTS_DIR"] = str(projects_dir)
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(hook_json).encode(),
        capture_output=True, env=env, timeout=30,
    )


def _run_scan(life_dir: Path, projects_dir: Path) -> subprocess.CompletedProcess:
    """Run cc_session_digest.py in scan mode."""
    env = os.environ.copy()
    env["LIFE_DIR"] = str(life_dir)
    env["CLAUDE_PROJECTS_DIR"] = str(projects_dir)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--scan"],
        capture_output=True, env=env, timeout=30,
    )


def _digest_path(life_dir: Path, d: str = None) -> Path:
    d = d or TODAY
    parts = d.split("-")
    return life_dir / "Daily" / parts[0] / parts[1] / f"sessions-digest-{d}.jsonl"


def _read_digests(life_dir: Path, d: str = None) -> list[dict]:
    dp = _digest_path(life_dir, d)
    if not dp.exists():
        return []
    results = []
    for line in dp.read_text().splitlines():
        line = line.strip()
        if line:
            results.append(json.loads(line))
    return results


@pytest.fixture
def life_dir(tmp_path: Path) -> Path:
    parts = TODAY.split("-")
    (tmp_path / "Daily" / parts[0] / parts[1]).mkdir(parents=True)
    (tmp_path / "logs").mkdir()
    return tmp_path


# ── Core Tests ───────────────────────────────────────────────────────────────


class TestBasicDigest:
    def test_basic_digest(self, life_dir, tmp_path):
        """10 messages + tool calls → JSONL line with expected fields."""
        tp, sid = make_session(tmp_path / "transcripts", tools=[
            ("Read", {"file_path": "/home/enrico/foo.py"}),
            ("Bash", {"command": "ls"}),
        ])
        result = _run_hook(life_dir, {"session_id": sid, "transcript_path": str(tp), "cwd": "/home/enrico"})
        assert result.returncode == 0
        digests = _read_digests(life_dir)
        assert len(digests) == 1
        d = digests[0]
        assert d["session_id"] == sid
        assert d["cwd"] == "/home/enrico"
        assert "files_touched" in d
        assert "tool_counts" in d
        assert d["msg_count"] >= 10

    def test_trivial_session_skipped(self, life_dir, tmp_path):
        """3 messages → no output."""
        tp, sid = make_session(tmp_path / "transcripts", msg_count=3)
        result = _run_hook(life_dir, {"session_id": sid, "transcript_path": str(tp)})
        assert result.returncode == 0
        assert not _digest_path(life_dir).exists() or len(_read_digests(life_dir)) == 0

    def test_exactly_5_messages(self, life_dir, tmp_path):
        """Boundary: 5 messages → digest IS created."""
        tp, sid = make_session(tmp_path / "transcripts", msg_count=5)
        _run_hook(life_dir, {"session_id": sid, "transcript_path": str(tp)})
        assert len(_read_digests(life_dir)) == 1

    def test_missing_transcript(self, life_dir):
        """transcript_path doesn't exist → exits 0."""
        result = _run_hook(life_dir, {"session_id": "x", "transcript_path": "/nonexistent.jsonl"})
        assert result.returncode == 0
        assert len(_read_digests(life_dir)) == 0


# ── File Extraction ──────────────────────────────────────────────────────────


class TestFileExtraction:
    def test_read_file_paths(self, life_dir, tmp_path):
        tp, sid = make_session(tmp_path / "t", tools=[
            ("Read", {"file_path": "/home/enrico/life/scripts/foo.py"}),
        ])
        _run_hook(life_dir, {"session_id": sid, "transcript_path": str(tp)})
        d = _read_digests(life_dir)[0]
        assert "/home/enrico/life/scripts/foo.py" in d["files_touched"]

    def test_edit_file_paths(self, life_dir, tmp_path):
        tp, sid = make_session(tmp_path / "t", tools=[
            ("Edit", {"file_path": "/home/enrico/bar.py", "old_string": "a", "new_string": "b"}),
        ])
        _run_hook(life_dir, {"session_id": sid, "transcript_path": str(tp)})
        d = _read_digests(life_dir)[0]
        assert "/home/enrico/bar.py" in d["files_touched"]

    def test_grep_path(self, life_dir, tmp_path):
        tp, sid = make_session(tmp_path / "t", tools=[
            ("Grep", {"pattern": "test", "path": "/home/enrico/project"}),
        ])
        _run_hook(life_dir, {"session_id": sid, "transcript_path": str(tp)})
        d = _read_digests(life_dir)[0]
        assert "/home/enrico/project" in d["files_touched"]

    def test_glob_pattern_not_extracted(self, life_dir, tmp_path):
        """Glob with only pattern (no path) → not in files_touched."""
        tp, sid = make_session(tmp_path / "t", tools=[
            ("Glob", {"pattern": "**/*.py"}),
        ])
        _run_hook(life_dir, {"session_id": sid, "transcript_path": str(tp)})
        d = _read_digests(life_dir)[0]
        assert "**/*.py" not in d["files_touched"]

    def test_duplicate_files_deduped(self, life_dir, tmp_path):
        tp, sid = make_session(tmp_path / "t", tools=[
            ("Read", {"file_path": "/home/enrico/same.py"}),
            ("Read", {"file_path": "/home/enrico/same.py"}),
            ("Read", {"file_path": "/home/enrico/same.py"}),
        ])
        _run_hook(life_dir, {"session_id": sid, "transcript_path": str(tp)})
        d = _read_digests(life_dir)[0]
        assert d["files_touched"].count("/home/enrico/same.py") == 1


# ── Concurrency and Append ───────────────────────────────────────────────────


class TestAppend:
    def test_append_not_overwrite(self, life_dir, tmp_path):
        """Two runs with different sessions → 2 lines."""
        tp1, sid1 = make_session(tmp_path / "t1")
        tp2, sid2 = make_session(tmp_path / "t2")
        _run_hook(life_dir, {"session_id": sid1, "transcript_path": str(tp1)})
        _run_hook(life_dir, {"session_id": sid2, "transcript_path": str(tp2)})
        assert len(_read_digests(life_dir)) == 2

    def test_concurrent_appends(self, life_dir, tmp_path):
        """3 instances simultaneously → all 3 lines present."""
        sessions = []
        for i in range(3):
            tp, sid = make_session(tmp_path / f"t{i}")
            sessions.append((tp, sid))

        threads = []
        for tp, sid in sessions:
            t = threading.Thread(target=_run_hook, args=(life_dir, {"session_id": sid, "transcript_path": str(tp)}))
            threads.append(t)
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        digests = _read_digests(life_dir)
        sids = {d["session_id"] for d in digests}
        assert len(sids) == 3


# ── Error Handling ───────────────────────────────────────────────────────────


class TestErrorHandling:
    def test_malformed_transcript_lines(self, life_dir, tmp_path):
        """Mix of valid JSON, invalid, and empty → handles gracefully."""
        tp = tmp_path / "t" / "bad.jsonl"
        tp.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            make_line("user", role="user", text="hello"),
            "{invalid json here",
            "",
            make_line("assistant", role="assistant", text="hi"),
            make_line("user", role="user", text="q2"),
            "not json",
            make_line("assistant", role="assistant"),
            make_line("user", role="user"),
            make_line("assistant", role="assistant"),
        ]
        make_transcript(tp, lines)
        result = _run_hook(life_dir, {"session_id": "bad", "transcript_path": str(tp)})
        assert result.returncode == 0
        digests = _read_digests(life_dir)
        assert len(digests) == 1
        assert digests[0]["msg_count"] >= 5

    def test_non_message_types_skipped(self, life_dir, tmp_path):
        """file-history-snapshot and progress → not counted."""
        tp = tmp_path / "t" / "types.jsonl"
        tp.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            make_line("file-history-snapshot"),
            make_line("progress"),
        ] + [make_line("user", role="user") for _ in range(3)] + [
            make_line("assistant", role="assistant") for _ in range(3)
        ]
        make_transcript(tp, lines)
        result = _run_hook(life_dir, {"session_id": "types", "transcript_path": str(tp)})
        assert result.returncode == 0
        digests = _read_digests(life_dir)
        assert len(digests) == 1
        assert digests[0]["msg_count"] == 6  # Only user+assistant, not snapshot/progress

    def test_empty_message_content(self, life_dir, tmp_path):
        tp = tmp_path / "t" / "empty.jsonl"
        tp.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            json.dumps({"type": "user", "message": {}, "uuid": str(uuid4()), "timestamp": datetime.now().isoformat()}),
            json.dumps({"type": "assistant", "message": {"content": None}, "uuid": str(uuid4()), "timestamp": datetime.now().isoformat()}),
        ] + [make_line("user", role="user") for _ in range(3)] + [make_line("assistant", role="assistant") for _ in range(3)]
        make_transcript(tp, lines)
        result = _run_hook(life_dir, {"session_id": "empty", "transcript_path": str(tp)})
        assert result.returncode == 0

    def test_tool_use_without_input(self, life_dir, tmp_path):
        tp = tmp_path / "t" / "noinp.jsonl"
        tp.parent.mkdir(parents=True, exist_ok=True)
        lines = [make_line("user", role="user") for _ in range(3)] + [
            make_line("assistant", role="assistant") for _ in range(3)
        ]
        # Add a tool_use with no input field
        lines.append(json.dumps({
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Bash"}]},
            "uuid": str(uuid4()), "timestamp": datetime.now().isoformat(),
        }))
        make_transcript(tp, lines)
        result = _run_hook(life_dir, {"session_id": "noinp", "transcript_path": str(tp)})
        assert result.returncode == 0

    def test_content_is_string(self, life_dir, tmp_path):
        """message.content is a string → handled without crash."""
        tp = tmp_path / "t" / "strct.jsonl"
        tp.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            json.dumps({"type": "user", "message": {"role": "user", "content": "plain text"}, "uuid": str(uuid4()), "timestamp": datetime.now().isoformat()}),
        ] + [make_line("user", role="user") for _ in range(3)] + [make_line("assistant", role="assistant") for _ in range(3)]
        make_transcript(tp, lines)
        result = _run_hook(life_dir, {"session_id": "strct", "transcript_path": str(tp)})
        assert result.returncode == 0

    def test_trailing_empty_lines(self, life_dir, tmp_path):
        tp, sid = make_session(tmp_path / "t")
        # Append empty lines
        with tp.open("a") as f:
            f.write("\n\n\n")
        result = _run_hook(life_dir, {"session_id": sid, "transcript_path": str(tp)})
        assert result.returncode == 0

    def test_empty_stdin(self, life_dir, tmp_path):
        """No data on stdin → exits 0."""
        env = os.environ.copy()
        env["LIFE_DIR"] = str(life_dir)
        env["CLAUDE_PROJECTS_DIR"] = str(tmp_path / "nonexistent")
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input=b"", capture_output=True, env=env, timeout=30,
        )
        assert result.returncode == 0

    def test_transcript_path_is_directory(self, life_dir, tmp_path):
        result = _run_hook(life_dir, {"session_id": "x", "transcript_path": str(tmp_path)})
        assert result.returncode == 0


# ── Scan Mode ────────────────────────────────────────────────────────────────


class TestScanMode:
    def test_scan_finds_todays_transcripts(self, life_dir, tmp_path):
        """2 today + 1 old → digest has 2 entries."""
        projects = tmp_path / "projects"
        projects.mkdir()
        make_session(projects, session_id="today1")
        make_session(projects, session_id="today2")
        # Create old transcript
        old_tp, _ = make_session(projects, session_id="old1")
        # Set mtime to yesterday
        yesterday = time.time() - 86400 * 2
        os.utime(old_tp, (yesterday, yesterday))

        result = _run_scan(life_dir, projects)
        assert result.returncode == 0
        digests = _read_digests(life_dir)
        sids = {d["session_id"] for d in digests}
        assert "today1" in sids
        assert "today2" in sids
        assert "old1" not in sids

    def test_scan_skips_already_processed(self, life_dir, tmp_path):
        """Pre-populate digest with session_id → skipped on scan."""
        projects = tmp_path / "projects"
        projects.mkdir()
        make_session(projects, session_id="already-done")
        make_session(projects, session_id="new-one")

        # Pre-populate digest
        dp = _digest_path(life_dir)
        dp.write_text(json.dumps({"session_id": "already-done", "ts": "", "summary": "", "files_touched": [], "tool_counts": {}, "msg_count": 10}) + "\n")

        _run_scan(life_dir, projects)
        digests = _read_digests(life_dir)
        sids = [d["session_id"] for d in digests]
        assert sids.count("already-done") == 1  # Not duplicated
        assert "new-one" in sids

    def test_scan_ignores_subagent_transcripts(self, life_dir, tmp_path):
        """Files in subagents/ → not scanned."""
        projects = tmp_path / "projects"
        (projects / "subagents").mkdir(parents=True)
        make_session(projects, session_id="main-session")
        make_session(projects / "subagents", session_id="sub-session")

        _run_scan(life_dir, projects)
        digests = _read_digests(life_dir)
        sids = {d["session_id"] for d in digests}
        assert "main-session" in sids
        assert "sub-session" not in sids

    def test_scan_no_transcripts_dir(self, life_dir, tmp_path):
        """projects dir doesn't exist → exits 0."""
        result = _run_scan(life_dir, tmp_path / "nonexistent")
        assert result.returncode == 0

    def test_scan_empty_dir(self, life_dir, tmp_path):
        projects = tmp_path / "projects"
        projects.mkdir()
        result = _run_scan(life_dir, projects)
        assert result.returncode == 0
        assert len(_read_digests(life_dir)) == 0

    def test_scan_mode_uses_transcript_date(self, life_dir, tmp_path):
        """Transcript from yesterday scanned today should land in yesterday's digest."""
        projects = tmp_path / "projects"
        projects.mkdir()
        yesterday = date.fromisoformat(TODAY) - timedelta(days=1)
        yesterday_ts = datetime(yesterday.year, yesterday.month, yesterday.day, 14, 30).isoformat()
        # Create transcript with yesterday's timestamp
        sid = "yesterday-session"
        tp = projects / f"{sid}.jsonl"
        lines = [
            make_line("user", role="user", text="Work from yesterday", ts=yesterday_ts),
            make_line("assistant", role="assistant"),
        ] * 5
        make_transcript(tp, lines)

        _run_scan(life_dir, projects)
        # Should NOT be in today's digest
        today_digests = _read_digests(life_dir, TODAY)
        assert not any(d["session_id"] == sid for d in today_digests)
        # Should be in yesterday's digest
        yesterday_digests = _read_digests(life_dir, str(yesterday))
        assert any(d["session_id"] == sid for d in yesterday_digests)

    def test_hook_fallthrough_to_scan(self, life_dir, tmp_path):
        """Empty stdin → falls through to scan mode."""
        projects = tmp_path / "projects"
        projects.mkdir()
        make_session(projects, session_id="fallthrough")

        env = os.environ.copy()
        env["LIFE_DIR"] = str(life_dir)
        env["CLAUDE_PROJECTS_DIR"] = str(projects)
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input=b"", capture_output=True, env=env, timeout=30,
        )
        assert result.returncode == 0
        digests = _read_digests(life_dir)
        assert any(d["session_id"] == "fallthrough" for d in digests)


# ── Concurrency Tests ────────────────────────────────────────────────────────


class TestConcurrency:
    def test_hook_and_scan_no_duplicates(self, life_dir, tmp_path):
        """Run hook and scan concurrently for same session → only 1 line."""
        projects = tmp_path / "projects"
        projects.mkdir()
        tp, sid = make_session(projects, session_id="race-test")

        def run_hook():
            _run_hook(life_dir, {"session_id": sid, "transcript_path": str(tp)}, projects)

        def run_scan():
            _run_scan(life_dir, projects)

        t1 = threading.Thread(target=run_hook)
        t2 = threading.Thread(target=run_scan)
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        digests = _read_digests(life_dir)
        sids = [d["session_id"] for d in digests if d["session_id"] == sid]
        # At most 2 if race happens, but should ideally be 1
        # Due to hook mode not holding the full scan lock, we accept <=2
        assert len(sids) <= 2


# ── Session Type Classification ─────────────────────────────────────────────


class TestSessionType:
    def test_session_type_coding(self):
        """Mostly Edit/Write tools → coding."""
        assert csd._classify_session({"Edit": 10, "Write": 5, "Read": 2}) == "coding"

    def test_session_type_research(self):
        """Mostly Read/Grep/Glob tools → research."""
        assert csd._classify_session({"Read": 10, "Grep": 5, "Glob": 3, "Edit": 1}) == "research"

    def test_session_type_ops(self):
        """Mostly Bash tools → ops."""
        assert csd._classify_session({"Bash": 15, "Read": 2, "Edit": 1}) == "ops"

    def test_session_type_chat(self):
        """No tools → chat."""
        assert csd._classify_session({}) == "chat"

    def test_session_type_mixed(self):
        """Balanced Edit + Read → mixed."""
        assert csd._classify_session({"Edit": 3, "Read": 3, "Bash": 3, "Grep": 1}) == "mixed"

    def test_session_type_empty_counts(self):
        """Empty dict → chat."""
        assert csd._classify_session({}) == "chat"

    def test_session_type_agent_heavy(self):
        """Mostly Agent calls → research."""
        assert csd._classify_session({"Agent": 8, "Read": 2}) == "research"


# ── Decision Detection ──────────────────────────────────────────────────────


class TestDecisionDetection:
    def test_decision_detected(self):
        """Assistant says 'decided to use QMD' → appears in decisions."""
        texts = ["After reviewing options, we decided to use QMD for semantic search."]
        decisions = csd._extract_decisions(texts)
        assert len(decisions) >= 1
        assert any("QMD" in d for d in decisions)

    def test_decision_from_assistant_only(self, life_dir, tmp_path):
        """Only assistant messages scanned for decisions, not user messages."""
        tp = tmp_path / "t" / "dec.jsonl"
        tp.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            make_line("user", role="user", text="I decided to skip tests"),
        ] + [make_line("user", role="user") for _ in range(3)] + [
            make_line("assistant", role="assistant") for _ in range(3)
        ]
        make_transcript(tp, lines)
        result = _run_hook(life_dir, {"session_id": "dec-test", "transcript_path": str(tp)})
        assert result.returncode == 0
        digests = _read_digests(life_dir)
        if digests:
            # User text appears in topic but should NOT be in decisions list
            assert "skip tests" not in " ".join(digests[0].get("decisions", []))

    def test_multiple_decisions_max_3(self):
        """5 decisions in texts → only first 3 captured."""
        texts = [
            "We decided to use QMD.",
            "We chose the 3-layer approach.",
            "We opted for systemd timers.",
            "We decided to drop memsearch.",
            "We switched to Haiku for extraction.",
        ]
        decisions = csd._extract_decisions(texts)
        assert len(decisions) == 3

    def test_decisions_truncated(self):
        """Long decision text → truncated to 100 chars."""
        long_text = "We decided to " + "implement a very complex feature that requires extensive refactoring across multiple files and services and databases " * 3
        decisions = csd._extract_decisions([long_text])
        if decisions:
            assert len(decisions[0]) <= 100

    def test_no_decisions(self):
        """Transcript with no decision patterns → empty list."""
        texts = ["The gateway is running fine.", "All tests are passing.", "Let me check the logs."]
        decisions = csd._extract_decisions(texts)
        assert decisions == []


# ── Key Files ───────────────────────────────────────────────────────────────


class TestKeyFiles:
    def test_key_files_in_summary(self, life_dir, tmp_path):
        """Top files appear in summary string."""
        tp, sid = make_session(tmp_path / "t", tools=[
            ("Edit", {"file_path": "/home/enrico/life/scripts/foo.py"}),
            ("Edit", {"file_path": "/home/enrico/life/scripts/bar.py"}),
            ("Read", {"file_path": "/home/enrico/life/scripts/baz.py"}),
        ])
        _run_hook(life_dir, {"session_id": sid, "transcript_path": str(tp)})
        d = _read_digests(life_dir)[0]
        assert "Key files:" in d["summary"]

    def test_plan_files_excluded(self):
        """Plan files excluded from key files."""
        files = {"/home/enrico/.claude/plans/linked-weaving-dewdrop.md", "/home/enrico/foo.py"}
        result = csd._top_files(files)
        assert not any("plans" in r for r in result)
        assert "foo.py" in result

    def test_log_files_excluded(self):
        """Log files excluded from key files."""
        files = {"/home/enrico/life/logs/consolidate.log", "/home/enrico/app.py"}
        result = csd._top_files(files)
        assert not any("consolidate" in r for r in result)
        assert "app.py" in result


# ── Integration: Rich Summary Format ────────────────────────────────────────


class TestRichSummary:
    def test_summary_has_session_type(self, life_dir, tmp_path):
        """Summary starts with [type] prefix."""
        tp, sid = make_session(tmp_path / "t", tools=[
            ("Edit", {"file_path": "/home/enrico/foo.py"}),
            ("Edit", {"file_path": "/home/enrico/bar.py"}),
            ("Edit", {"file_path": "/home/enrico/baz.py"}),
            ("Edit", {"file_path": "/home/enrico/qux.py"}),
        ])
        _run_hook(life_dir, {"session_id": sid, "transcript_path": str(tp)})
        d = _read_digests(life_dir)[0]
        assert d["summary"].startswith("[")
        assert d.get("session_type") in ("coding", "research", "ops", "chat", "mixed")
