"""Tests for cc_start_hook.sh — session context briefing at session start."""
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
    (tmp_path / "Areas" / "about-me").mkdir(parents=True)
    (tmp_path / "Areas" / "about-me" / "hard-rules.md").write_text("No secrets.\n")
    (tmp_path / "Areas" / "about-me" / "profile.md").write_text("Engineer.\n")
    return tmp_path


def _run_hook(life_dir: Path, cwd: str = "/tmp") -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["LIFE_DIR"] = str(life_dir)
    return subprocess.run(
        ["bash", str(HOOK_SCRIPT)],
        capture_output=True, text=True, env=env, timeout=15, cwd=cwd,
    )


def _maxwell_path(life_dir: Path) -> Path:
    parts = TODAY.split("-")
    return life_dir / "Daily" / parts[0] / parts[1] / f"maxwell-{TODAY}.md"


def _daily_path(life_dir: Path) -> Path:
    parts = TODAY.split("-")
    return life_dir / "Daily" / parts[0] / parts[1] / f"{TODAY}.md"


class TestCoreHook:
    def test_exits_zero(self, life_dir):
        result = _run_hook(life_dir)
        assert result.returncode == 0

    def test_includes_hard_rules(self, life_dir):
        result = _run_hook(life_dir)
        assert "No secrets" in result.stdout

    def test_includes_profile(self, life_dir):
        result = _run_hook(life_dir)
        assert "Engineer" in result.stdout

    def test_with_maxwell_file(self, life_dir):
        _maxwell_path(life_dir).write_text(
            "---\ndate: " + TODAY + "\nsource: maxwell-ingest\n---\n\n"
            "## Maxwell Dispatch Activity\n"
            "- 10:30 — **Pixel** (coder): Test Task (16s, success)\n",
            encoding="utf-8",
        )
        result = _run_hook(life_dir)
        assert result.returncode == 0
        assert "Pixel" in result.stdout

    def test_daily_note_active_projects(self, life_dir):
        _daily_path(life_dir).write_text(
            "---\ndate: today\n---\n"
            "## Active Projects\n- [[pi-cluster]] — deploying v2\n"
            "## Pending Items\n- Fix gateway\n",
        )
        result = _run_hook(life_dir)
        assert "pi-cluster" in result.stdout
        assert "deploying v2" in result.stdout


class TestEdgeCases:
    def test_output_under_10k_chars(self, life_dir):
        result = _run_hook(life_dir)
        assert len(result.stdout) < 10000

    def test_maxwell_file_frontmatter_only(self, life_dir):
        _maxwell_path(life_dir).write_text(
            "---\ndate: " + TODAY + "\nsource: maxwell-ingest\n---\n",
            encoding="utf-8",
        )
        result = _run_hook(life_dir)
        assert result.returncode == 0

    def test_maxwell_file_crlf(self, life_dir):
        _maxwell_path(life_dir).write_text(
            "---\r\ndate: " + TODAY + "\r\n---\r\n\r\n"
            "## Maxwell Dispatch Activity\r\n"
            "- 10:30 — **Pixel** (coder): CRLF Task\r\n",
            encoding="utf-8",
        )
        result = _run_hook(life_dir)
        assert result.returncode == 0
        assert "Pixel" in result.stdout

    def test_empty_life_dir_no_crash(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        (empty / "logs").mkdir()
        env = os.environ.copy()
        env["LIFE_DIR"] = str(empty)
        result = subprocess.run(
            ["bash", str(HOOK_SCRIPT)],
            capture_output=True, text=True, env=env, timeout=15,
        )
        assert result.returncode == 0
