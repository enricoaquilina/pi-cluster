"""Tests for skill_loader.py — progressive-disclosure skill matching."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import skill_loader


@pytest.fixture
def skills_dir(tmp_path):
    life = tmp_path / "life"
    sd = life / "Resources" / "skills"
    sd.mkdir(parents=True)
    with patch.object(skill_loader, "LIFE_DIR", life), \
         patch.object(skill_loader, "SKILLS_DIR", sd):
        yield sd


def _write_skill(skills_dir, name, display, triggers=None, content=""):
    triggers_line = ""
    if triggers:
        triggers_line = f"triggers: [{', '.join(triggers)}]\n"
    (skills_dir / f"{name}.md").write_text(
        f"---\ntype: skill\nname: {display}\ncreated: 2026-04-24\n{triggers_line}---\n\n{content}\n"
    )


class TestLoadSkills:
    def test_loads_all_skills(self, skills_dir):
        _write_skill(skills_dir, "deploy", "Deploy to Cluster")
        _write_skill(skills_dir, "recover", "Disaster Recovery")
        assert len(skill_loader.load_skills()) == 2

    def test_empty_dir(self, skills_dir):
        assert skill_loader.load_skills() == []

    def test_skips_non_skill_files(self, skills_dir):
        (skills_dir / "notes.md").write_text("---\ntype: note\n---\nNot a skill\n")
        assert skill_loader.load_skills() == []


class TestMatchSkills:
    def test_trigger_match(self, skills_dir):
        _write_skill(skills_dir, "deploy", "Deploy", triggers=["deploy", "rollback", "ansible"])
        _write_skill(skills_dir, "health", "Health Check", triggers=["health", "monitoring"])
        results = skill_loader.match_skills("deploy the app")
        assert len(results) == 1
        assert results[0]["name"] == "Deploy"

    def test_content_fallback(self, skills_dir):
        _write_skill(skills_dir, "deploy", "Deploy", content="Use ansible playbook to deploy")
        results = skill_loader.match_skills("ansible")
        assert len(results) == 1

    def test_name_match(self, skills_dir):
        _write_skill(skills_dir, "disaster", "Disaster Recovery Validation")
        results = skill_loader.match_skills("disaster recovery")
        assert len(results) == 1

    def test_no_match(self, skills_dir):
        _write_skill(skills_dir, "deploy", "Deploy", triggers=["deploy"])
        results = skill_loader.match_skills("cooking recipe")
        assert len(results) == 0

    def test_max_results_respected(self, skills_dir):
        for i in range(10):
            _write_skill(skills_dir, f"skill{i}", f"Skill {i}", content="common keyword")
        results = skill_loader.match_skills("common keyword", max_results=3)
        assert len(results) == 3

    def test_trigger_ranks_higher_than_content(self, skills_dir):
        _write_skill(skills_dir, "triggered", "Triggered", triggers=["deploy"])
        _write_skill(skills_dir, "content", "Content Match", content="deploy something here")
        results = skill_loader.match_skills("deploy")
        assert results[0]["name"] == "Triggered"

    def test_skills_without_triggers_still_searchable(self, skills_dir):
        _write_skill(skills_dir, "maxwell", "Maxwell Sync Runbook", content="sync openclaw memory gateway")
        results = skill_loader.match_skills("maxwell sync")
        assert len(results) == 1
