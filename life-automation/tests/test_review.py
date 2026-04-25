"""Tests for candidates.py and review.py — fact staging + graduation."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import candidates


@pytest.fixture
def life_dir(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    with patch.object(candidates, "LIFE_DIR", tmp_path), \
         patch.object(candidates, "CANDIDATES_PATH", logs / "candidates.jsonl"), \
         patch.object(candidates, "REVIEW_QUEUE_PATH", tmp_path / "REVIEW_QUEUE.md"):
        candidates._counter = 0
        yield tmp_path


def _make_entity(life_dir, slug, entity_type="project", facts=None):
    type_to_dir = {"person": "People", "project": "Projects", "company": "Companies"}
    base = type_to_dir.get(entity_type, "Resources")
    d = life_dir / base / slug
    d.mkdir(parents=True, exist_ok=True)
    items = facts or []
    (d / "items.json").write_text(json.dumps(items), encoding="utf-8")
    (d / "summary.md").write_text(f"---\ntype: {entity_type}\n---\n", encoding="utf-8")
    return d


class TestStageFact:
    def test_stages_new_candidate(self, life_dir):
        result = candidates.stage_fact(
            "pi-cluster", "project", "Deployed v2", "deployment", "2026-04-24"
        )
        assert result["id"].startswith("cand-")
        assert result["status"] == "pending"
        assert result["fact"] == "Deployed v2"
        assert result["mentions"] == 1

    def test_deduplicates_similar_fact(self, life_dir):
        candidates.stage_fact("pi-cluster", "project", "Deployed v2 of gateway", "deployment", "2026-04-24")
        result = candidates.stage_fact("pi-cluster", "project", "Deployed v2 of gateway", "deployment", "2026-04-25")
        assert result["mentions"] == 2
        assert result["last_seen"] == "2026-04-25"
        assert len(candidates.pending_candidates()) == 1

    def test_different_entity_not_deduped(self, life_dir):
        candidates.stage_fact("pi-cluster", "project", "Deployed v2", "deployment", "2026-04-24")
        candidates.stage_fact("other-project", "project", "Deployed v2", "deployment", "2026-04-24")
        assert len(candidates.pending_candidates()) == 2

    def test_decision_category_needs_review(self, life_dir):
        result = candidates.stage_fact("pi-cluster", "project", "Chose Postgres over SQLite", "decision", "2026-04-24")
        assert result["needs_review"] is True

    def test_lesson_category_needs_review(self, life_dir):
        result = candidates.stage_fact("pi-cluster", "project", "Always test first", "lesson", "2026-04-24")
        assert result["needs_review"] is True

    def test_deployment_category_auto_graduatable(self, life_dir):
        result = candidates.stage_fact("pi-cluster", "project", "Deployed app", "deployment", "2026-04-24")
        assert result["needs_review"] is False

    def test_contradiction_flag_forces_review(self, life_dir):
        result = candidates.stage_fact(
            "pi-cluster", "project", "Uses SQLite", "configuration", "2026-04-24",
            contradicts_existing=True,
        )
        assert result["needs_review"] is True

    def test_supersedes_stored(self, life_dir):
        result = candidates.stage_fact(
            "pi-cluster", "project", "Gateway uses v3", "configuration", "2026-04-24",
            supersedes="Gateway uses v2",
        )
        assert result["supersedes"] == "Gateway uses v2"


class TestAutoGraduatable:
    def test_auto_graduate_categories(self, life_dir):
        candidates.stage_fact("pi-cluster", "project", "Deployed app", "deployment", "2026-04-24")
        assert len(candidates.auto_graduatable()) == 1

    def test_review_categories_excluded_when_fresh(self, life_dir):
        candidates.stage_fact("pi-cluster", "project", "Chose X over Y", "decision", "2026-04-24")
        assert len(candidates.auto_graduatable()) == 0

    def test_review_category_auto_graduates_after_7_days(self, life_dir):
        """Decisions/lessons auto-graduate after AUTO_GRADUATE_DAYS (silence = approval)."""
        from datetime import datetime, timezone, timedelta
        c = candidates.stage_fact("pi-cluster", "project", "Important decision", "decision", "2026-04-24")
        # Age the candidate past the threshold
        old_created = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        all_cands = candidates._load_all()
        for entry in all_cands:
            if entry["id"] == c["id"]:
                entry["created"] = old_created
        candidates._save_all(all_cands)
        assert len(candidates.auto_graduatable()) == 1
        assert candidates.auto_graduatable()[0]["id"] == c["id"]

    def test_review_category_not_graduated_before_threshold(self, life_dir):
        """Decisions younger than AUTO_GRADUATE_DAYS stay in review."""
        from datetime import datetime, timezone, timedelta
        c = candidates.stage_fact("pi-cluster", "project", "Recent decision", "decision", "2026-04-24")
        recent_created = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        all_cands = candidates._load_all()
        for entry in all_cands:
            if entry["id"] == c["id"]:
                entry["created"] = recent_created
        candidates._save_all(all_cands)
        assert len(candidates.auto_graduatable()) == 0

    def test_lesson_category_auto_graduates_after_threshold(self, life_dir):
        """Lessons also auto-graduate after the time threshold."""
        from datetime import datetime, timezone, timedelta
        c = candidates.stage_fact("pi-cluster", "project", "Old lesson", "lesson", "2026-04-15")
        old_created = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        all_cands = candidates._load_all()
        for entry in all_cands:
            if entry["id"] == c["id"]:
                entry["created"] = old_created
        candidates._save_all(all_cands)
        assert len(candidates.auto_graduatable()) == 1

    def test_mention_threshold_graduates(self, life_dir):
        candidates.stage_fact("pi-cluster", "project", "Unknown category fact", "unknown_cat", "2026-04-24")
        assert len(candidates.auto_graduatable()) == 0
        candidates.stage_fact("pi-cluster", "project", "Unknown category fact", "unknown_cat", "2026-04-25")
        assert len(candidates.auto_graduatable()) == 1


class TestGraduate:
    def test_graduate_writes_to_items_json(self, life_dir):
        _make_entity(life_dir, "pi-cluster")
        c = candidates.stage_fact("pi-cluster", "project", "Deployed v2", "deployment", "2026-04-24")
        result = candidates.graduate(c["id"], "verified")
        assert result["status"] == "graduated"
        assert result["graduated_at"] is not None

        items = json.loads((life_dir / "Projects" / "pi-cluster" / "items.json").read_text())
        assert len(items) == 1
        assert items[0]["fact"] == "Deployed v2"

    def test_graduate_reinforces_existing_fact(self, life_dir):
        existing = [{"fact": "Deployed v2", "mentions": 1, "confidence": "single", "last_seen": "2026-04-20"}]
        _make_entity(life_dir, "pi-cluster", facts=existing)
        c = candidates.stage_fact("pi-cluster", "project", "Deployed v2", "deployment", "2026-04-24")
        candidates.graduate(c["id"])
        items = json.loads((life_dir / "Projects" / "pi-cluster" / "items.json").read_text())
        assert len(items) == 1
        assert items[0]["mentions"] == 2
        assert items[0]["confidence"] == "confirmed"

    def test_graduate_handles_supersedes(self, life_dir):
        existing = [{"fact": "Uses gateway v1", "confidence": "confirmed", "mentions": 3}]
        _make_entity(life_dir, "pi-cluster", facts=existing)
        c = candidates.stage_fact(
            "pi-cluster", "project", "Uses gateway v2", "configuration", "2026-04-24",
            supersedes="Uses gateway v1",
        )
        candidates.graduate(c["id"])
        items = json.loads((life_dir / "Projects" / "pi-cluster" / "items.json").read_text())
        old = [i for i in items if "v1" in i["fact"]][0]
        assert old["confidence"] == "superseded"
        assert old["superseded_by"] == "Uses gateway v2"

    def test_graduate_nonexistent_entity_returns_none(self, life_dir):
        c = candidates.stage_fact("nonexistent", "project", "Something", "event", "2026-04-24")
        assert candidates.graduate(c["id"]) is None

    def test_graduate_already_graduated_returns_none(self, life_dir):
        _make_entity(life_dir, "pi-cluster")
        c = candidates.stage_fact("pi-cluster", "project", "Fact", "event", "2026-04-24")
        candidates.graduate(c["id"])
        assert candidates.graduate(c["id"]) is None

    def test_graduate_person_entity_type(self, life_dir):
        _make_entity(life_dir, "alice", entity_type="person")
        c = candidates.stage_fact("alice", "person", "Joined team", "event", "2026-04-24")
        result = candidates.graduate(c["id"])
        assert result is not None
        items = json.loads((life_dir / "People" / "alice" / "items.json").read_text())
        assert len(items) == 1


class TestReject:
    def test_reject_sets_status(self, life_dir):
        c = candidates.stage_fact("pi-cluster", "project", "Wrong fact", "event", "2026-04-24")
        result = candidates.reject(c["id"], "inaccurate")
        assert result["status"] == "rejected"
        assert result["rationale"] == "inaccurate"
        assert len(candidates.pending_candidates()) == 0

    def test_reject_nonexistent_returns_none(self, life_dir):
        assert candidates.reject("cand-nonexistent") is None


class TestCheckSupersedesCandidates:
    def test_finds_matching_candidate(self, life_dir):
        candidates.stage_fact("pi-cluster", "project", "Uses gateway v1", "config", "2026-04-24")
        result = candidates.check_supersedes_candidates("Uses gateway v1", "pi-cluster")
        assert result is not None

    def test_no_match_returns_none(self, life_dir):
        result = candidates.check_supersedes_candidates("something", "pi-cluster")
        assert result is None


class TestReviewQueue:
    def test_empty_queue(self, life_dir):
        md = candidates.generate_review_queue()
        assert "No pending candidates" in md

    def test_queue_with_review_items(self, life_dir):
        candidates.stage_fact("pi-cluster", "project", "Big decision", "decision", "2026-04-24")
        candidates.stage_fact("pi-cluster", "project", "Normal deploy", "deployment", "2026-04-24")
        md = candidates.generate_review_queue()
        assert "Needs Review (1)" in md
        assert "Auto-Graduatable (1)" in md
        assert "Big decision" in md

    def test_write_review_queue_creates_file(self, life_dir):
        candidates.stage_fact("pi-cluster", "project", "Something", "event", "2026-04-24")
        candidates.write_review_queue()
        assert candidates.REVIEW_QUEUE_PATH.exists()


class TestNotifyCommand:
    """review.py notify — Telegram-formatted review summary."""

    def test_notify_with_review_items(self, life_dir):
        candidates.stage_fact("pi-cluster", "project", "Chose X", "decision", "2026-04-24")
        candidates.stage_fact("pi-cluster", "project", "Deploy v2", "deployment", "2026-04-24")
        import subprocess
        result = subprocess.run(
            [sys.executable, str(Path(__file__).parent.parent / "review.py"), "notify"],
            capture_output=True, text=True,
            env={**__import__("os").environ, "LIFE_DIR": str(life_dir)},
        )
        assert result.returncode == 0
        assert "Nightly Review Queue" in result.stdout
        assert "1 need human review" in result.stdout
        assert "Chose X" in result.stdout
        assert "1 auto-graduatable" in result.stdout

    def test_notify_empty_exits_nonzero(self, life_dir):
        import subprocess
        result = subprocess.run(
            [sys.executable, str(Path(__file__).parent.parent / "review.py"), "notify"],
            capture_output=True, text=True,
            env={**__import__("os").environ, "LIFE_DIR": str(life_dir)},
        )
        assert result.returncode == 1
        assert result.stdout.strip() == ""

    def test_notify_truncates_long_list(self, life_dir):
        for i in range(8):
            candidates.stage_fact(
                "pi-cluster", "project", f"Decision number {i}", "decision", "2026-04-24"
            )
        import subprocess
        result = subprocess.run(
            [sys.executable, str(Path(__file__).parent.parent / "review.py"), "notify"],
            capture_output=True, text=True,
            env={**__import__("os").environ, "LIFE_DIR": str(life_dir)},
        )
        assert result.returncode == 0
        assert "and 3 more" in result.stdout
