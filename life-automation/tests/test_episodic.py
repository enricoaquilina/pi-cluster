"""Tests for episodic.py — cross-platform event logging."""
from __future__ import annotations

import json
import sys
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import episodic


@pytest.fixture
def logs_dir(tmp_path):
    d = tmp_path / "life" / "logs"
    d.mkdir(parents=True)
    with patch.object(episodic, "LOGS_DIR", d):
        yield d


class TestLogEvent:
    def test_creates_file_and_writes_valid_jsonl(self, logs_dir):
        result = episodic.log_event(
            "claude-code", "fact_added",
            entity="pi-cluster", detail="Test fact", importance=7,
        )
        assert result is True

        today = date.today()
        path = logs_dir / f"episodic-{today.strftime('%Y-%m')}.jsonl"
        assert path.exists()

        lines = path.read_text().strip().splitlines()
        assert len(lines) == 1

        entry = json.loads(lines[0])
        assert entry["platform"] == "claude-code"
        assert entry["event"] == "fact_added"
        assert entry["entity"] == "pi-cluster"
        assert entry["detail"] == "Test fact"
        assert entry["importance"] == 7
        assert "ts" in entry

    def test_appends_multiple_entries(self, logs_dir):
        episodic.log_event("claude-code", "fact_added", detail="First")
        episodic.log_event("maxwell", "dispatch_ingested", detail="Second")
        episodic.log_event("hermes", "session_ended", detail="Third")

        today = date.today()
        path = logs_dir / f"episodic-{today.strftime('%Y-%m')}.jsonl"
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 3

        platforms = [json.loads(line)["platform"] for line in lines]
        assert platforms == ["claude-code", "maxwell", "hermes"]

    def test_truncates_detail_to_500_chars(self, logs_dir):
        long_detail = "x" * 1000
        episodic.log_event("claude-code", "fact_added", detail=long_detail)

        today = date.today()
        path = logs_dir / f"episodic-{today.strftime('%Y-%m')}.jsonl"
        entry = json.loads(path.read_text().strip())
        assert len(entry["detail"]) == 500

    def test_clamps_importance(self, logs_dir):
        episodic.log_event("claude-code", "fact_added", importance=-5)
        episodic.log_event("claude-code", "fact_added", importance=99)

        today = date.today()
        path = logs_dir / f"episodic-{today.strftime('%Y-%m')}.jsonl"
        lines = path.read_text().strip().splitlines()
        assert json.loads(lines[0])["importance"] == 0
        assert json.loads(lines[1])["importance"] == 10

    def test_monthly_rotation_filename(self, logs_dir):
        path = episodic._log_path(date(2026, 4, 15))
        assert path.name == "episodic-2026-04.jsonl"

        path2 = episodic._log_path(date(2026, 5, 1))
        assert path2.name == "episodic-2026-05.jsonl"

    def test_creates_logs_dir_if_missing(self, tmp_path):
        new_logs = tmp_path / "newlife" / "logs"
        with patch.object(episodic, "LOGS_DIR", new_logs):
            result = episodic.log_event("claude-code", "fact_added", detail="auto-create")
        assert result is True
        assert new_logs.exists()

    def test_concurrent_writes(self, logs_dir):
        results = []

        def writer(platform, n):
            for i in range(n):
                r = episodic.log_event(platform, "fact_added", detail=f"entry-{i}")
                results.append(r)

        threads = [
            threading.Thread(target=writer, args=("claude-code", 10)),
            threading.Thread(target=writer, args=("maxwell", 10)),
            threading.Thread(target=writer, args=("hermes", 10)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        today = date.today()
        path = logs_dir / f"episodic-{today.strftime('%Y-%m')}.jsonl"
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 30
        for line in lines:
            json.loads(line)  # all valid JSON


class TestRecentActivity:
    def _seed_entries(self, logs_dir, entries):
        today = date.today()
        path = logs_dir / f"episodic-{today.strftime('%Y-%m')}.jsonl"
        with open(path, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

    def test_filters_by_time_window(self, logs_dir):
        now = datetime.now(timezone.utc)
        old = now - timedelta(hours=48)

        self._seed_entries(logs_dir, [
            {"ts": old.isoformat(), "platform": "claude-code", "event": "old"},
            {"ts": now.isoformat(), "platform": "maxwell", "event": "recent"},
        ])

        result = episodic.recent_activity(hours=24)
        assert len(result) == 1
        assert result[0]["event"] == "recent"

    def test_filters_by_platform(self, logs_dir):
        now = datetime.now(timezone.utc)
        self._seed_entries(logs_dir, [
            {"ts": now.isoformat(), "platform": "claude-code", "event": "a"},
            {"ts": now.isoformat(), "platform": "maxwell", "event": "b"},
            {"ts": now.isoformat(), "platform": "hermes", "event": "c"},
        ])

        result = episodic.recent_activity(platforms=["maxwell", "hermes"])
        assert len(result) == 2
        platforms = {e["platform"] for e in result}
        assert platforms == {"maxwell", "hermes"}

    def test_respects_limit(self, logs_dir):
        now = datetime.now(timezone.utc)
        entries = [
            {"ts": now.isoformat(), "platform": "claude-code", "event": f"e{i}"}
            for i in range(50)
        ]
        self._seed_entries(logs_dir, entries)

        result = episodic.recent_activity(limit=5)
        assert len(result) == 5

    def test_returns_newest_first(self, logs_dir):
        now = datetime.now(timezone.utc)
        entries = [
            {"ts": (now - timedelta(hours=i)).isoformat(), "platform": "claude-code", "event": f"e{i}"}
            for i in range(5)
        ]
        self._seed_entries(logs_dir, entries)

        result = episodic.recent_activity()
        timestamps = [e["ts"] for e in result]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_empty_log_returns_empty(self, logs_dir):
        result = episodic.recent_activity()
        assert result == []

    def test_reads_previous_month_at_boundary(self, logs_dir):
        now = datetime.now(timezone.utc)
        prev_month = (date.today().replace(day=1) - timedelta(days=1))
        prev_path = logs_dir / f"episodic-{prev_month.strftime('%Y-%m')}.jsonl"
        with open(prev_path, "w") as f:
            f.write(json.dumps({
                "ts": now.isoformat(),
                "platform": "maxwell",
                "event": "from_prev_month",
            }) + "\n")

        result = episodic.recent_activity(hours=24)
        assert len(result) == 1
        assert result[0]["event"] == "from_prev_month"


class TestPlatformSummary:
    def test_counts_by_platform(self, logs_dir):
        now = datetime.now(timezone.utc)
        path = logs_dir / f"episodic-{date.today().strftime('%Y-%m')}.jsonl"
        with open(path, "w") as f:
            for _ in range(3):
                f.write(json.dumps({"ts": now.isoformat(), "platform": "claude-code", "event": "a"}) + "\n")
            for _ in range(2):
                f.write(json.dumps({"ts": now.isoformat(), "platform": "maxwell", "event": "b"}) + "\n")

        result = episodic.platform_summary(hours=24)
        assert result["claude-code"] == 3
        assert result["maxwell"] == 2
