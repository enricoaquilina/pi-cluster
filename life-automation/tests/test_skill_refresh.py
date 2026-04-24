"""Tests for skill_refresh.py — self-updating skills."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import skill_refresh
import episodic


@pytest.fixture
def life_dir(tmp_path):
    life = tmp_path / "life"
    skills = life / "Resources" / "skills"
    skills.mkdir(parents=True)
    logs = life / "logs"
    logs.mkdir()
    with patch.object(skill_refresh, "LIFE_DIR", life), \
         patch.object(skill_refresh, "SKILLS_DIR", skills), \
         patch.object(skill_refresh, "BACKUP_DIR", logs / "skill-backups"), \
         patch.object(episodic, "LOGS_DIR", logs):
        yield life


def _write_skill(life_dir, name, display, triggers=None, auto_refresh=True, use_count=5):
    skills = life_dir / "Resources" / "skills"
    triggers_line = ""
    if triggers:
        triggers_line = f"triggers: [{', '.join(triggers)}]\n"
    (skills / f"{name}.md").write_text(
        f"---\ntype: skill\nname: {display}\ncreated: 2026-04-24\n"
        f"auto_refresh: {'true' if auto_refresh else 'false'}\n"
        f"use_count: {use_count}\n"
        f"{triggers_line}---\n\n## Steps\n1. Do the thing\n"
    )


def _seed_episodic(life_dir, entries):
    from datetime import date
    path = life_dir / "logs" / f"episodic-{date.today().strftime('%Y-%m')}.jsonl"
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _make_event(detail, entity="", hours_ago=1):
    ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {
        "ts": ts.isoformat(),
        "platform": "claude-code",
        "event": "fact_added",
        "detail": detail,
        "entity": entity,
        "importance": 5,
    }


class TestFindRefreshCandidates:
    def test_finds_qualifying_skill(self, life_dir):
        _write_skill(life_dir, "deploy", "Deploy", triggers=["deploy"], use_count=5)
        _seed_episodic(life_dir, [_make_event("deploy new version", "pi-cluster")])
        candidates = skill_refresh.find_refresh_candidates()
        assert len(candidates) == 1
        assert candidates[0]["name"] == "Deploy"

    def test_skips_low_use_count(self, life_dir):
        _write_skill(life_dir, "deploy", "Deploy", triggers=["deploy"], use_count=1)
        _seed_episodic(life_dir, [_make_event("deploy new version")])
        assert len(skill_refresh.find_refresh_candidates()) == 0

    def test_skips_auto_refresh_false(self, life_dir):
        _write_skill(life_dir, "deploy", "Deploy", triggers=["deploy"], auto_refresh=False)
        _seed_episodic(life_dir, [_make_event("deploy new version")])
        assert len(skill_refresh.find_refresh_candidates()) == 0

    def test_skips_no_recent_activity(self, life_dir):
        _write_skill(life_dir, "deploy", "Deploy", triggers=["deploy"], use_count=5)
        assert len(skill_refresh.find_refresh_candidates()) == 0

    def test_uses_stem_as_fallback_trigger(self, life_dir):
        _write_skill(life_dir, "cluster-deployment", "Deploy", use_count=5)
        _seed_episodic(life_dir, [_make_event("cluster deployment completed")])
        candidates = skill_refresh.find_refresh_candidates()
        assert len(candidates) == 1


class TestRefreshSkill:
    def test_creates_backup(self, life_dir):
        _write_skill(life_dir, "deploy", "Deploy", triggers=["deploy"], use_count=5)
        _seed_episodic(life_dir, [_make_event("deploy v2")])
        candidates = skill_refresh.find_refresh_candidates()
        skill_refresh.refresh_skill(candidates[0])
        backups = list((life_dir / "logs" / "skill-backups").glob("*.md"))
        assert len(backups) == 1
        assert "deploy" in backups[0].name

    def test_dry_run_no_backup(self, life_dir):
        _write_skill(life_dir, "deploy", "Deploy", triggers=["deploy"], use_count=5)
        _seed_episodic(life_dir, [_make_event("deploy v2")])
        candidates = skill_refresh.find_refresh_candidates()
        skill_refresh.refresh_skill(candidates[0], dry_run=True)
        backup_dir = life_dir / "logs" / "skill-backups"
        assert not backup_dir.exists() or len(list(backup_dir.glob("*.md"))) == 0

    def test_updates_frontmatter(self, life_dir):
        _write_skill(life_dir, "deploy", "Deploy", triggers=["deploy"], use_count=5)
        _seed_episodic(life_dir, [_make_event("deploy v2")])
        candidates = skill_refresh.find_refresh_candidates()
        skill_refresh.refresh_skill(candidates[0])
        text = (life_dir / "Resources" / "skills" / "deploy.md").read_text()
        assert "last_refreshed" in text


class TestBuildRefreshContext:
    def test_includes_event_details(self, life_dir):
        _write_skill(life_dir, "deploy", "Deploy", triggers=["deploy"], use_count=5)
        _seed_episodic(life_dir, [_make_event("deploy v2 to production", "pi-cluster")])
        candidates = skill_refresh.find_refresh_candidates()
        context = skill_refresh.build_refresh_context(candidates[0])
        assert "deploy v2 to production" in context
        assert "pi-cluster" in context
