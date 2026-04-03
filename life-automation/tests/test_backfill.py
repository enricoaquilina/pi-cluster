import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

SCRIPT = Path(__file__).parent.parent / "backfill_history.py"


@pytest.fixture
def life_dir(tmp_path):
    """Minimal ~/life/ for backfill testing."""
    (tmp_path / "Projects/pi-cluster").mkdir(parents=True)
    (tmp_path / "Projects/pi-cluster/items.json").write_text("[]")
    (tmp_path / "Projects/pi-cluster/summary.md").write_text("---\ntype: project\n---\n")
    (tmp_path / "Projects/_template").mkdir()
    (tmp_path / "People").mkdir()
    (tmp_path / "Companies").mkdir()
    (tmp_path / "relationships.json").write_text("[]")
    (tmp_path / "logs").mkdir()
    return tmp_path


def run_backfill(life_dir, extra_env=None):
    env = os.environ.copy()
    env["LIFE_DIR"] = str(life_dir)
    env["MC_API_URL"] = "http://localhost:3000/api"
    env["MC_API_KEY"] = "test-key"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run"],
        capture_output=True, text=True, env=env, timeout=30,
    )


class TestEnsureEntity:
    def test_creates_missing_entity(self, life_dir):
        """Backfill should create entity folders that don't exist."""
        sys.path.insert(0, str(SCRIPT.parent))
        import importlib
        import backfill_history as bh
        importlib.reload(bh)
        bh.LIFE_DIR = life_dir
        bh.DRY_RUN = False

        polybot_dir = life_dir / "Projects" / "polymarket-bot"
        assert not polybot_dir.exists()

        bh.ensure_entity("project", "polymarket-bot", "Polymarket Bot")
        assert polybot_dir.exists()
        assert (polybot_dir / "items.json").exists()
        assert (polybot_dir / "summary.md").exists()

    def test_existing_entity_not_overwritten(self, life_dir):
        """Existing items.json should not be replaced."""
        items = [{"fact": "existing fact", "category": "test"}]
        (life_dir / "Projects/pi-cluster/items.json").write_text(json.dumps(items))

        r = run_backfill(life_dir)
        assert r.returncode == 0

        # Existing fact should still be there
        result_items = json.loads((life_dir / "Projects/pi-cluster/items.json").read_text())
        assert any(i["fact"] == "existing fact" for i in result_items)


class TestClassifyPR:
    def test_feat_is_deployment(self):
        sys.path.insert(0, str(SCRIPT.parent))
        import importlib
        import backfill_history as bh
        importlib.reload(bh)
        assert bh.classify_pr("feat: add billing monitoring") == "deployment"

    def test_fix_is_lesson(self):
        from backfill_history import classify_pr
        assert classify_pr("fix: bash anti-pattern in smoke test") == "lesson"

    def test_refactor_is_configuration(self):
        from backfill_history import classify_pr
        assert classify_pr("refactor: split monolith into modules") == "configuration"

    def test_unknown_is_event(self):
        from backfill_history import classify_pr
        assert classify_pr("something else entirely") == "event"

    def test_chore_is_configuration(self):
        from backfill_history import classify_pr
        assert classify_pr("chore: update dependencies") == "configuration"

    def test_ci_is_configuration(self):
        from backfill_history import classify_pr
        assert classify_pr("ci: fix pipeline") == "configuration"


class TestAddFact:
    def test_adds_new_fact(self, life_dir):
        sys.path.insert(0, str(SCRIPT.parent))
        import importlib
        import backfill_history as bh
        importlib.reload(bh)
        bh.LIFE_DIR = life_dir
        bh.DRY_RUN = False

        items_path = life_dir / "Projects/pi-cluster/items.json"
        result = bh.add_fact(items_path, "Test fact", "event", "2026-03-15", "backfill/test")
        assert result is True

        items = json.loads(items_path.read_text())
        assert len(items) == 1
        assert items[0]["fact"] == "Test fact"
        assert items[0]["source"] == "backfill/test"

    def test_skips_duplicate(self, life_dir):
        sys.path.insert(0, str(SCRIPT.parent))
        import importlib
        import backfill_history as bh
        importlib.reload(bh)
        bh.LIFE_DIR = life_dir
        bh.DRY_RUN = False

        items_path = life_dir / "Projects/pi-cluster/items.json"
        bh.add_fact(items_path, "Same fact", "event", "2026-03-15", "backfill/test")
        result = bh.add_fact(items_path, "Same fact", "event", "2026-03-16", "backfill/test")
        assert result is False

        items = json.loads(items_path.read_text())
        assert len(items) == 1

    def test_source_marked_as_backfill(self, life_dir):
        sys.path.insert(0, str(SCRIPT.parent))
        import importlib
        import backfill_history as bh
        importlib.reload(bh)
        bh.LIFE_DIR = life_dir
        bh.DRY_RUN = False

        items_path = life_dir / "Projects/pi-cluster/items.json"
        bh.add_fact(items_path, "PR #99 merged", "deployment", "2026-03-30", "backfill/github-pr-99")

        items = json.loads(items_path.read_text())
        assert items[0]["source"].startswith("backfill/")

    def test_dry_run_returns_true_but_no_write(self, life_dir):
        sys.path.insert(0, str(SCRIPT.parent))
        import importlib
        import backfill_history as bh
        importlib.reload(bh)
        bh.LIFE_DIR = life_dir
        bh.DRY_RUN = True

        items_path = life_dir / "Projects/pi-cluster/items.json"
        result = bh.add_fact(items_path, "Dry fact", "event", "2026-03-15", "backfill/test")
        assert result is True

        items = json.loads(items_path.read_text())
        assert len(items) == 0


class TestAddRelationship:
    def test_adds_new_relationship(self, life_dir):
        sys.path.insert(0, str(SCRIPT.parent))
        import importlib
        import backfill_history as bh
        importlib.reload(bh)
        bh.LIFE_DIR = life_dir
        bh.DRY_RUN = False

        result = bh.add_relationship("scout", "person", "pi-cluster", "project", "works-on", "2026-03-10", "backfill/dispatch")
        assert result is True

        rels = json.loads((life_dir / "relationships.json").read_text())
        assert len(rels) == 1
        assert rels[0]["from"] == "scout"

    def test_skips_duplicate_relationship(self, life_dir):
        sys.path.insert(0, str(SCRIPT.parent))
        import importlib
        import backfill_history as bh
        importlib.reload(bh)
        bh.LIFE_DIR = life_dir
        bh.DRY_RUN = False

        bh.add_relationship("scout", "person", "pi-cluster", "project", "works-on", "2026-03-10", "backfill/dispatch")
        result = bh.add_relationship("scout", "person", "pi-cluster", "project", "works-on", "2026-03-15", "backfill/dispatch")
        assert result is False

        rels = json.loads((life_dir / "relationships.json").read_text())
        assert len(rels) == 1

    def test_dry_run_returns_true_but_no_write(self, life_dir):
        sys.path.insert(0, str(SCRIPT.parent))
        import importlib
        import backfill_history as bh
        importlib.reload(bh)
        bh.LIFE_DIR = life_dir
        bh.DRY_RUN = True

        result = bh.add_relationship("scout", "person", "pi-cluster", "project", "works-on", "2026-03-10", "backfill/dispatch")
        assert result is True

        rels = json.loads((life_dir / "relationships.json").read_text())
        assert len(rels) == 0


class TestDryRun:
    def test_dry_run_no_files_changed(self, life_dir):
        """Dry run should not create any files or modify existing ones."""
        items_before = (life_dir / "Projects/pi-cluster/items.json").read_text()
        rels_before = (life_dir / "relationships.json").read_text()

        r = run_backfill(life_dir)
        assert r.returncode == 0

        assert (life_dir / "Projects/pi-cluster/items.json").read_text() == items_before
        assert (life_dir / "relationships.json").read_text() == rels_before


class TestBackfillIntegration:
    def test_script_runs_without_crashing(self, life_dir):
        """Even with no API access, script should exit 0."""
        r = run_backfill(life_dir)
        assert r.returncode == 0
        assert "backfill" in r.stdout.lower()

    def test_output_contains_source_counts(self, life_dir):
        """Output should report counts per source."""
        r = run_backfill(life_dir)
        assert r.returncode == 0
        assert "github_prs" in r.stdout
        assert "mc_tasks" in r.stdout
        assert "dispatch_log" in r.stdout
        assert "claude_memory" in r.stdout


class TestSafeLoadJson:
    def test_missing_file_returns_empty_list(self, tmp_path):
        sys.path.insert(0, str(SCRIPT.parent))
        import importlib
        import backfill_history as bh
        importlib.reload(bh)

        result = bh.safe_load_json(tmp_path / "nonexistent.json")
        assert result == []

    def test_invalid_json_returns_empty_list(self, tmp_path):
        sys.path.insert(0, str(SCRIPT.parent))
        import importlib
        import backfill_history as bh
        importlib.reload(bh)

        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json at all {{{")
        result = bh.safe_load_json(bad_file)
        assert result == []

    def test_valid_json_returns_data(self, tmp_path):
        sys.path.insert(0, str(SCRIPT.parent))
        import importlib
        import backfill_history as bh
        importlib.reload(bh)

        good_file = tmp_path / "good.json"
        good_file.write_text('[{"key": "value"}]')
        result = bh.safe_load_json(good_file)
        assert result == [{"key": "value"}]


class TestIsDuplicate:
    def test_detects_duplicate(self):
        sys.path.insert(0, str(SCRIPT.parent))
        import importlib
        import backfill_history as bh
        importlib.reload(bh)

        items = [{"fact": "existing fact"}, {"fact": "another fact"}]
        assert bh.is_duplicate(items, "existing fact") is True

    def test_not_duplicate(self):
        from backfill_history import is_duplicate
        items = [{"fact": "existing fact"}]
        assert is_duplicate(items, "new fact") is False

    def test_empty_list(self):
        from backfill_history import is_duplicate
        assert is_duplicate([], "anything") is False
