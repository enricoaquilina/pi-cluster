"""Tests for cc_start_hook.sh — Maxwell context briefing at session start."""
import json
import os
import subprocess
from datetime import date
from pathlib import Path

import pytest

HOOK_SCRIPT = Path(__file__).parent.parent / "cc_start_hook.sh"
TODAY = str(date.today())


@pytest.fixture
def life_dir(tmp_path: Path) -> Path:
    parts = TODAY.split("-")
    (tmp_path / "Daily" / parts[0] / parts[1]).mkdir(parents=True)
    (tmp_path / "logs").mkdir()
    return tmp_path


def _run_hook(life_dir: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["LIFE_DIR"] = str(life_dir)
    return subprocess.run(
        ["bash", str(HOOK_SCRIPT)],
        capture_output=True, text=True, env=env, timeout=15,
    )


def _maxwell_path(life_dir: Path) -> Path:
    parts = TODAY.split("-")
    return life_dir / "Daily" / parts[0] / parts[1] / f"maxwell-{TODAY}.md"


# ── Core Tests ───────────────────────────────────────────────────────────────


class TestCoreHook:
    def test_with_maxwell_file(self, life_dir):
        """maxwell-*.md with dispatch lines → stdout contains them."""
        _maxwell_path(life_dir).write_text(
            "---\ndate: " + TODAY + "\nsource: maxwell-ingest\n---\n\n"
            "## Maxwell Dispatch Activity\n"
            "- 10:30 — **Pixel** (coder): Test Task (16s, success)\n",
            encoding="utf-8",
        )
        result = _run_hook(life_dir)
        assert result.returncode == 0
        assert "Pixel" in result.stdout
        assert "Test Task" in result.stdout

    def test_no_files(self, life_dir):
        """No maxwell or agent-runs → says 'No Maxwell dispatches', exits 0."""
        result = _run_hook(life_dir)
        assert result.returncode == 0
        assert "No Maxwell dispatches" in result.stdout

    def test_with_agent_runs(self, life_dir):
        """agent-runs.json with 5 entries → stdout shows last 3."""
        runs = []
        for i in range(5):
            runs.append({
                "timestamp": f"{TODAY}T{10+i}:00:00",
                "items_checked": 1,
                "actions": [{"title": f"Task {i}", "action": "dispatched", "persona": f"Agent{i}"}],
                "nodes": {},
                "budget_spent_today": 0,
            })
        (life_dir / "logs" / "agent-runs.json").write_text(json.dumps(runs), encoding="utf-8")
        result = _run_hook(life_dir)
        assert result.returncode == 0
        # Should show last 3 entries (Task 2, 3, 4)
        assert "Task 4" in result.stdout
        assert "Task 3" in result.stdout
        assert "Task 2" in result.stdout


# ── Edge Cases ───────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_output_under_10k_chars(self, life_dir):
        """Large agent-runs.json → stdout < 10,000 chars."""
        runs = []
        for i in range(100):
            runs.append({
                "timestamp": f"{TODAY}T{i % 24:02d}:00:00",
                "items_checked": 1,
                "actions": [{"title": f"Task {i} with a long title that goes on", "action": "awaiting_approval", "persona": "Pixel"}],
                "nodes": {},
                "budget_spent_today": 0,
            })
        (life_dir / "logs" / "agent-runs.json").write_text(json.dumps(runs), encoding="utf-8")
        result = _run_hook(life_dir)
        assert result.returncode == 0
        assert len(result.stdout) < 10000

    def test_agent_runs_null_fields(self, life_dir):
        """Entries with null persona, missing actions → no crash."""
        runs = [
            {"timestamp": f"{TODAY}T10:00:00", "items_checked": 0, "actions": [], "nodes": {}, "budget_spent_today": 0},
            {"timestamp": f"{TODAY}T11:00:00", "actions": [{"title": "X", "persona": None, "action": None}]},
            {"timestamp": f"{TODAY}T12:00:00"},  # Missing actions entirely
        ]
        (life_dir / "logs" / "agent-runs.json").write_text(json.dumps(runs), encoding="utf-8")
        result = _run_hook(life_dir)
        assert result.returncode == 0

    def test_maxwell_file_frontmatter_only(self, life_dir):
        """File with only YAML frontmatter → no crash."""
        _maxwell_path(life_dir).write_text(
            "---\ndate: " + TODAY + "\nsource: maxwell-ingest\n---\n",
            encoding="utf-8",
        )
        result = _run_hook(life_dir)
        assert result.returncode == 0

    def test_agent_runs_partial_json(self, life_dir):
        """Truncated JSON → no crash, shows fallback."""
        (life_dir / "logs" / "agent-runs.json").write_text('[{"timestamp": "' + TODAY, encoding="utf-8")
        result = _run_hook(life_dir)
        assert result.returncode == 0
        assert "Could not read" in result.stdout

    def test_maxwell_file_crlf(self, life_dir):
        """Windows-style line endings → handled."""
        _maxwell_path(life_dir).write_text(
            "---\r\ndate: " + TODAY + "\r\n---\r\n\r\n"
            "## Maxwell Dispatch Activity\r\n"
            "- 10:30 — **Pixel** (coder): CRLF Task\r\n",
            encoding="utf-8",
        )
        result = _run_hook(life_dir)
        assert result.returncode == 0
        assert "Pixel" in result.stdout
