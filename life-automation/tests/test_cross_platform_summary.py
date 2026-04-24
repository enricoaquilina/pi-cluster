"""Tests for cross_platform_summary.py — cross-platform activity view."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import episodic
import cross_platform_summary as cps


@pytest.fixture
def logs_dir(tmp_path):
    d = tmp_path / "life" / "logs"
    d.mkdir(parents=True)
    with patch.object(episodic, "LOGS_DIR", d):
        yield d


def _seed(logs_dir, entries):
    today = date.today()
    path = logs_dir / f"episodic-{today.strftime('%Y-%m')}.jsonl"
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _make_entry(platform, event="fact_added", detail="test detail", entity="pi-cluster", hours_ago=0):
    ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {
        "ts": ts.isoformat(),
        "platform": platform,
        "event": event,
        "detail": detail,
        "entity": entity,
        "importance": 5,
        "run_id": "",
        "source_file": "",
    }


class TestFormatSummary:
    def test_empty_log_returns_empty_string(self, logs_dir):
        result = cps.format_summary(hours=24)
        assert result == ""

    def test_groups_by_platform(self, logs_dir):
        _seed(logs_dir, [
            _make_entry("maxwell", detail="deployed app"),
            _make_entry("maxwell", detail="ran tests"),
            _make_entry("hermes", detail="searched docs"),
        ])
        result = cps.format_summary(hours=24)
        assert "Maxwell/OpenClaw" in result
        assert "Hermes Agent" in result
        assert "2 events" in result

    def test_exclude_platform(self, logs_dir):
        _seed(logs_dir, [
            _make_entry("claude-code", detail="wrote code"),
            _make_entry("maxwell", detail="deployed"),
        ])
        result = cps.format_summary(hours=24, exclude_platforms=["claude-code"])
        assert "Claude Code" not in result
        assert "Maxwell" in result

    def test_max_lines_respected(self, logs_dir):
        entries = [_make_entry("maxwell", detail=f"action {i}") for i in range(20)]
        _seed(logs_dir, entries)
        result = cps.format_summary(hours=24, max_lines=5)
        lines = result.strip().splitlines()
        assert len(lines) <= 5

    def test_old_entries_filtered(self, logs_dir):
        _seed(logs_dir, [
            _make_entry("maxwell", detail="old", hours_ago=48),
            _make_entry("hermes", detail="recent", hours_ago=1),
        ])
        result = cps.format_summary(hours=24)
        assert "old" not in result
        assert "recent" in result

    def test_header_contains_hours(self, logs_dir):
        _seed(logs_dir, [_make_entry("maxwell")])
        result = cps.format_summary(hours=12)
        assert "last 12h" in result

    def test_shows_event_type_counts(self, logs_dir):
        _seed(logs_dir, [
            _make_entry("maxwell", event="fact_added"),
            _make_entry("maxwell", event="fact_added"),
            _make_entry("maxwell", event="entity_created"),
        ])
        result = cps.format_summary(hours=24)
        assert "2 fact added" in result
        assert "1 entity created" in result

    def test_entity_prefix_in_detail(self, logs_dir):
        _seed(logs_dir, [_make_entry("maxwell", entity="pi-cluster", detail="deployed v2")])
        result = cps.format_summary(hours=24)
        assert "[pi-cluster]" in result

    def test_no_entity_no_prefix(self, logs_dir):
        _seed(logs_dir, [_make_entry("maxwell", entity="", detail="generic action")])
        result = cps.format_summary(hours=24)
        assert "generic action" in result
        assert "[]" not in result
