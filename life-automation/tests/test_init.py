"""Tests for init.sh — verifies PARA knowledge structure creation.

Runs init.sh in an isolated temp HOME and validates directories, files,
content, and JSON validity. No mocking — real subprocess execution.
"""
import json
import os
import subprocess
from datetime import date
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "init.sh"
TODAY = str(date.today())
YEAR = TODAY[:4]
MONTH = TODAY[5:7]


@pytest.fixture
def init_home(tmp_path):
    """Run init.sh with HOME set to tmp_path, return the life dir."""
    # Create minimal .claude memory dir so migrate-memory.sh doesn't fail
    claude_mem = tmp_path / ".claude" / "projects" / "-home-enrico" / "memory"
    claude_mem.mkdir(parents=True)
    (claude_mem / "MEMORY.md").write_text("# Memory\n")

    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    result = subprocess.run(
        ["/bin/bash", str(SCRIPT)],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert result.returncode == 0, f"init.sh failed: {result.stderr}"
    return tmp_path / "life"


# ── Directory Structure ────────────────────────────────────────────────────


class TestDirectoryStructure:
    def test_creates_projects_dir(self, init_home):
        assert (init_home / "Projects").is_dir()

    def test_creates_areas_about_me(self, init_home):
        assert (init_home / "Areas" / "about-me").is_dir()

    def test_creates_resources_dir(self, init_home):
        assert (init_home / "Resources").is_dir()

    def test_creates_resources_skills(self, init_home):
        assert (init_home / "Resources" / "skills").is_dir()

    def test_creates_archives_dir(self, init_home):
        assert (init_home / "Archives").is_dir()

    def test_creates_people_dir(self, init_home):
        assert (init_home / "People").is_dir()

    def test_creates_companies_dir(self, init_home):
        assert (init_home / "Companies").is_dir()

    def test_creates_daily_hierarchy(self, init_home):
        assert (init_home / "Daily" / YEAR / MONTH).is_dir()

    def test_creates_logs_dir(self, init_home):
        assert (init_home / "logs").is_dir()

    def test_creates_scripts_dir(self, init_home):
        assert (init_home / "scripts").is_dir()


# ── Project Directories ───────────────────────────────────────────────────


class TestProjectDirs:
    def test_creates_pi_cluster(self, init_home):
        assert (init_home / "Projects" / "pi-cluster").is_dir()

    def test_creates_openclaw_maxwell(self, init_home):
        assert (init_home / "Projects" / "openclaw-maxwell").is_dir()

    def test_creates_polymarket_bot(self, init_home):
        assert (init_home / "Projects" / "polymarket-bot").is_dir()


# ── Templates ─────────────────────────────────────────────────────────────


class TestTemplates:
    def test_project_template_summary(self, init_home):
        assert (init_home / "Projects" / "_template" / "summary.md").exists()

    def test_project_template_items(self, init_home):
        path = init_home / "Projects" / "_template" / "items.json"
        assert path.exists()
        assert json.loads(path.read_text()) == []

    def test_people_template_summary(self, init_home):
        assert (init_home / "People" / "_template" / "summary.md").exists()

    def test_people_template_items(self, init_home):
        path = init_home / "People" / "_template" / "items.json"
        assert path.exists()
        assert json.loads(path.read_text()) == []

    def test_companies_template_summary(self, init_home):
        assert (init_home / "Companies" / "_template" / "summary.md").exists()

    def test_companies_template_items(self, init_home):
        path = init_home / "Companies" / "_template" / "items.json"
        assert path.exists()
        assert json.loads(path.read_text()) == []


# ── About-Me Files ────────────────────────────────────────────────────────


class TestAboutMe:
    def test_profile_exists(self, init_home):
        assert (init_home / "Areas" / "about-me" / "profile.md").exists()

    def test_hard_rules_exists(self, init_home):
        path = init_home / "Areas" / "about-me" / "hard-rules.md"
        assert path.exists()
        content = path.read_text()
        assert "email is never" in content.lower()

    def test_workflow_habits_exists(self, init_home):
        path = init_home / "Areas" / "about-me" / "workflow-habits.md"
        assert path.exists()
        content = path.read_text()
        assert "staged" in content.lower()

    def test_communication_preferences_exists(self, init_home):
        assert (init_home / "Areas" / "about-me" / "communication-preferences.md").exists()

    def test_lessons_learned_exists(self, init_home):
        path = init_home / "Areas" / "about-me" / "lessons-learned.md"
        assert path.exists()
        content = path.read_text()
        assert "conftest" in content.lower()


# ── CLAUDE.md ─────────────────────────────────────────────────────────────


class TestClaudeMd:
    def test_claude_md_created(self, init_home):
        assert (init_home.parent / "CLAUDE.md").exists()

    def test_claude_md_has_session_protocol(self, init_home):
        content = (init_home.parent / "CLAUDE.md").read_text()
        assert "Session Protocol" in content

    def test_claude_md_has_hard_rules_reference(self, init_home):
        content = (init_home.parent / "CLAUDE.md").read_text()
        assert "hard-rules" in content


# ── Daily Note ────────────────────────────────────────────────────────────


class TestDailyNote:
    def test_daily_note_created(self, init_home):
        path = init_home / "Daily" / YEAR / MONTH / f"{TODAY}.md"
        assert path.exists()

    def test_daily_note_has_date(self, init_home):
        path = init_home / "Daily" / YEAR / MONTH / f"{TODAY}.md"
        content = path.read_text()
        assert f"date: {TODAY}" in content

    def test_daily_note_has_sections(self, init_home):
        path = init_home / "Daily" / YEAR / MONTH / f"{TODAY}.md"
        content = path.read_text()
        for section in ["What We Worked On", "Decisions Made", "Pending Items", "Active Projects"]:
            assert section in content, f"Missing section: {section}"


# ── Forte Rule (no empty pre-created areas) ───────────────────────────────


class TestForteRule:
    def test_no_empty_finance_dir(self, init_home):
        """Forte rule: don't pre-create areas that have no content yet."""
        assert not (init_home / "Areas" / "finances").exists()

    def test_no_empty_health_dir(self, init_home):
        assert not (init_home / "Areas" / "health").exists()

    def test_no_empty_home_dir(self, init_home):
        assert not (init_home / "Areas" / "home").exists()


# ── Scripts Copied ────────────────────────────────────────────────────────


class TestScriptsCopied:
    def test_scripts_copied(self, init_home):
        """init.sh should copy automation scripts to ~/life/scripts/."""
        scripts_dir = init_home / "scripts"
        py_files = list(scripts_dir.glob("*.py"))
        sh_files = list(scripts_dir.glob("*.sh"))
        assert len(py_files) > 0, "No Python scripts copied"
        assert len(sh_files) > 0, "No shell scripts copied"

    def test_apply_extraction_copied(self, init_home):
        assert (init_home / "scripts" / "apply_extraction.py").exists()


# ── Idempotency ───────────────────────────────────────────────────────────


class TestIdempotency:
    def test_run_twice_no_error(self, tmp_path):
        """Running init.sh twice should not fail or duplicate content."""
        claude_mem = tmp_path / ".claude" / "projects" / "-home-enrico" / "memory"
        claude_mem.mkdir(parents=True)
        (claude_mem / "MEMORY.md").write_text("# Memory\n")

        env = os.environ.copy()
        env["HOME"] = str(tmp_path)
        for _ in range(2):
            result = subprocess.run(
                ["/bin/bash", str(SCRIPT)],
                capture_output=True, text=True, env=env, timeout=30,
            )
            assert result.returncode == 0
