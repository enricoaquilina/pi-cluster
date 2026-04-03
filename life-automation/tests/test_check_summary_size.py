"""Tests for check_summary_size.py — summary line limit warnings and escalations."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "check_summary_size.py"
TODAY = "2026-04-03"


@pytest.fixture
def life_dir(tmp_path: Path) -> Path:
    """Minimal ~/life/ with entity dirs."""
    for d in ["Projects/pi-cluster", "People/archie", "Companies/openrouter"]:
        (tmp_path / d).mkdir(parents=True)
    return tmp_path


def make_summary(life_dir: Path, entity_type: str, entity: str, lines: int) -> Path:
    """Create a summary.md with N lines."""
    path = life_dir / entity_type / entity / "summary.md"
    content = "\n".join(f"Line {i+1}" for i in range(lines))
    path.write_text(content, encoding="utf-8")
    return path


def make_items(life_dir: Path, entity_type: str, entity: str, items: list) -> Path:
    """Write items.json for an entity."""
    path = life_dir / entity_type / entity / "items.json"
    path.write_text(json.dumps(items, indent=2), encoding="utf-8")
    return path


def read_items(life_dir: Path, entity_type: str, entity: str) -> list:
    return json.loads((life_dir / entity_type / entity / "items.json").read_text())


def run_check(life_dir: Path, today: str = TODAY, extra_args: list = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["LIFE_DIR"] = str(life_dir)
    env["CONSOLIDATION_DATE"] = today
    cmd = [sys.executable, str(SCRIPT)]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.run(cmd, capture_output=True, env=env, text=True)


class TestSoftWarning:
    def test_no_warning_under_100_lines(self, life_dir):
        """Summary with 50 lines produces no WARNING."""
        make_summary(life_dir, "Projects", "pi-cluster", 50)
        result = run_check(life_dir)
        assert result.returncode == 0
        assert "WARNING" not in result.stderr
        assert "ESCALATION" not in result.stderr

    def test_soft_warning_at_101_lines(self, life_dir):
        """Summary with 101 lines produces WARNING."""
        make_summary(life_dir, "Projects", "pi-cluster", 101)
        result = run_check(life_dir)
        assert result.returncode == 0
        assert "WARNING" in result.stderr
        assert "pi-cluster" in result.stderr


class TestEscalation:
    def test_escalation_at_151_lines(self, life_dir):
        """Summary with 151 lines produces ESCALATION + pending item."""
        make_summary(life_dir, "Projects", "pi-cluster", 151)
        make_items(life_dir, "Projects", "pi-cluster", [])
        result = run_check(life_dir)
        assert result.returncode == 0
        assert "ESCALATION" in result.stderr
        items = read_items(life_dir, "Projects", "pi-cluster")
        assert len(items) == 1
        assert items[0]["category"] == "pending"
        assert "150 lines" in items[0]["fact"]

    def test_no_duplicate_pending(self, life_dir):
        """Running twice on 151-line summary creates only one pending item."""
        make_summary(life_dir, "Projects", "pi-cluster", 151)
        make_items(life_dir, "Projects", "pi-cluster", [])
        run_check(life_dir)
        run_check(life_dir)
        items = read_items(life_dir, "Projects", "pi-cluster")
        pending = [i for i in items if "150 lines" in i.get("fact", "")]
        assert len(pending) == 1

    def test_handles_missing_items_json(self, life_dir):
        """Entity with summary.md but no items.json — creates items.json."""
        make_summary(life_dir, "Projects", "pi-cluster", 160)
        # No items.json exists
        result = run_check(life_dir)
        assert result.returncode == 0
        items = read_items(life_dir, "Projects", "pi-cluster")
        assert len(items) == 1


class TestOperations:
    def test_dry_run_no_changes(self, life_dir):
        """--dry-run logs but does not write pending item."""
        make_summary(life_dir, "Projects", "pi-cluster", 160)
        make_items(life_dir, "Projects", "pi-cluster", [])
        result = run_check(life_dir, extra_args=["--dry-run"])
        assert result.returncode == 0
        assert "ESCALATION" in result.stderr
        items = read_items(life_dir, "Projects", "pi-cluster")
        assert len(items) == 0

    def test_handles_missing_summary(self, life_dir):
        """Entity dir with no summary.md — skipped gracefully."""
        # pi-cluster dir exists but no summary.md
        result = run_check(life_dir)
        assert result.returncode == 0

    def test_iterates_all_entity_types(self, life_dir):
        """Checks Projects, People, Companies."""
        make_summary(life_dir, "Projects", "pi-cluster", 110)
        make_summary(life_dir, "People", "archie", 105)
        make_summary(life_dir, "Companies", "openrouter", 102)
        result = run_check(life_dir)
        assert result.returncode == 0
        assert result.stderr.count("WARNING") == 3
