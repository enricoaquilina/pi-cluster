"""Tests for context_budget.py — tiered context assembly."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import context_budget


@pytest.fixture
def life_dir(tmp_path):
    life = tmp_path / "life"
    (life / "Areas" / "about-me").mkdir(parents=True)
    (life / "Areas" / "about-me" / "hard-rules.md").write_text("Never share secrets.\n")
    (life / "Areas" / "about-me" / "profile.md").write_text("Software engineer.\n")
    with patch.object(context_budget, "LIFE_DIR", life):
        yield life


class TestAssemble:
    def test_includes_hard_rules(self, life_dir):
        result = context_budget.assemble(budget=6000)
        assert "Hard Rules" in result
        assert "Never share secrets" in result

    def test_includes_profile(self, life_dir):
        result = context_budget.assemble(budget=6000)
        assert "Profile" in result
        assert "Software engineer" in result

    def test_respects_budget(self, life_dir):
        result = context_budget.assemble(budget=50)
        assert len(result) <= 200

    def test_empty_life_dir(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        import episodic
        with patch.object(context_budget, "LIFE_DIR", empty), \
             patch.object(episodic, "LOGS_DIR", empty / "logs"):
            result = context_budget.assemble(budget=6000)
            assert result == ""

    def test_daily_note_sections(self, life_dir):
        from datetime import date
        today = date.today()
        daily_dir = life_dir / "Daily" / today.strftime("%Y") / today.strftime("%m")
        daily_dir.mkdir(parents=True)
        (daily_dir / f"{today.isoformat()}.md").write_text(
            "---\ndate: today\n---\n"
            "## Active Projects\n- [[pi-cluster]] — deploying v2\n"
            "## Pending Items\n- Fix gateway bug\n"
        )
        result = context_budget.assemble(budget=6000)
        assert "Active Projects" in result
        assert "pi-cluster" in result
        assert "Pending Items" in result

    def test_project_detection(self, life_dir):
        (life_dir / "Projects" / "pi-cluster").mkdir(parents=True)
        (life_dir / "Projects" / "pi-cluster" / "summary.md").write_text(
            "---\ntype: project\n---\nPi cluster manages home infrastructure.\n"
        )
        config_dir = context_budget.SCRIPT_DIR / "config"
        slugs = config_dir / "project-slugs.json"
        if slugs.exists():
            result = context_budget.assemble(cwd="/home/enrico/pi-cluster", budget=6000)
            assert "pi-cluster" in result.lower()
