"""Tests for decay_facts.py — fact staleness, archival, backfill, edge cases."""
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "decay_facts.py"
TODAY = "2026-04-02"


@pytest.fixture
def life_dir(tmp_path: Path) -> Path:
    """Minimal ~/life/ with entity dirs."""
    for d in ["Projects/pi-cluster", "People/archie", "Companies/openrouter"]:
        (tmp_path / d).mkdir(parents=True)
    return tmp_path


def make_items(life_dir: Path, entity_type: str, entity: str, items: list) -> Path:
    """Write items.json for an entity."""
    path = life_dir / entity_type / entity / "items.json"
    path.write_text(json.dumps(items, indent=2), encoding="utf-8")
    return path


def run_decay(life_dir: Path, today: str = TODAY, extra_args: list = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["LIFE_DIR"] = str(life_dir)
    env["CONSOLIDATION_DATE"] = today
    cmd = [sys.executable, str(SCRIPT)]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.run(cmd, capture_output=True, env=env, text=True)


def read_items(life_dir: Path, entity_type: str, entity: str) -> list:
    return json.loads((life_dir / entity_type / entity / "items.json").read_text())


# ── Core Decay Rules ────────────────────────────────────────────────────────


class TestCoreDecay:
    def test_stale_after_14_days(self, life_dir):
        """Single-confidence fact, last_seen 15 days ago → stale."""
        old_date = str(date.fromisoformat(TODAY) - timedelta(days=15))
        make_items(life_dir, "Projects", "pi-cluster", [
            {"fact": "Gateway running", "confidence": "single", "last_seen": old_date, "mentions": 1, "date": old_date}
        ])
        run_decay(life_dir)
        items = read_items(life_dir, "Projects", "pi-cluster")
        assert items[0]["confidence"] == "stale"

    def test_confirmed_stale_after_14_days(self, life_dir):
        """Confirmed fact, last_seen 15 days ago → stale."""
        old_date = str(date.fromisoformat(TODAY) - timedelta(days=15))
        make_items(life_dir, "Projects", "pi-cluster", [
            {"fact": "Gateway v2", "confidence": "confirmed", "last_seen": old_date, "mentions": 2, "date": old_date}
        ])
        run_decay(life_dir)
        items = read_items(life_dir, "Projects", "pi-cluster")
        assert items[0]["confidence"] == "stale"

    def test_archived_after_30_days(self, life_dir):
        """Single fact, last_seen 31 days ago → archived."""
        old_date = str(date.fromisoformat(TODAY) - timedelta(days=31))
        make_items(life_dir, "Projects", "pi-cluster", [
            {"fact": "Old config", "confidence": "single", "last_seen": old_date, "mentions": 1, "date": old_date}
        ])
        run_decay(life_dir)
        items = read_items(life_dir, "Projects", "pi-cluster")
        assert items[0]["confidence"] == "archived"

    def test_established_gets_grace(self, life_dir):
        """Established fact (4+ mentions), last_seen 15 days ago → stays established."""
        old_date = str(date.fromisoformat(TODAY) - timedelta(days=15))
        make_items(life_dir, "Projects", "pi-cluster", [
            {"fact": "Core fact", "confidence": "established", "last_seen": old_date, "mentions": 5, "date": old_date}
        ])
        run_decay(life_dir)
        items = read_items(life_dir, "Projects", "pi-cluster")
        assert items[0]["confidence"] == "established"

    def test_established_stale_after_30_days(self, life_dir):
        """Established fact, last_seen 31 days → stale."""
        old_date = str(date.fromisoformat(TODAY) - timedelta(days=31))
        make_items(life_dir, "Projects", "pi-cluster", [
            {"fact": "Old established", "confidence": "established", "last_seen": old_date, "mentions": 5, "date": old_date}
        ])
        run_decay(life_dir)
        items = read_items(life_dir, "Projects", "pi-cluster")
        assert items[0]["confidence"] == "stale"

    def test_established_archived_after_60_days(self, life_dir):
        """Established fact, last_seen 61 days → archived."""
        old_date = str(date.fromisoformat(TODAY) - timedelta(days=61))
        make_items(life_dir, "Projects", "pi-cluster", [
            {"fact": "Ancient established", "confidence": "established", "last_seen": old_date, "mentions": 5, "date": old_date}
        ])
        run_decay(life_dir)
        items = read_items(life_dir, "Projects", "pi-cluster")
        assert items[0]["confidence"] == "archived"


# ── Skip Conditions ─────────────────────────────────────────────────────────


class TestSkipConditions:
    def test_already_archived_skipped(self, life_dir):
        """Archived fact not re-decayed."""
        old_date = str(date.fromisoformat(TODAY) - timedelta(days=90))
        make_items(life_dir, "Projects", "pi-cluster", [
            {"fact": "Already archived", "confidence": "archived", "last_seen": old_date, "mentions": 1, "date": old_date}
        ])
        run_decay(life_dir)
        items = read_items(life_dir, "Projects", "pi-cluster")
        assert items[0]["confidence"] == "archived"

    def test_superseded_skipped(self, life_dir):
        """Superseded fact not decayed."""
        make_items(life_dir, "Projects", "pi-cluster", [
            {"fact": "Old fact", "confidence": "superseded", "last_seen": "2026-01-01", "mentions": 1, "date": "2026-01-01"}
        ])
        run_decay(life_dir)
        items = read_items(life_dir, "Projects", "pi-cluster")
        assert items[0]["confidence"] == "superseded"

    def test_todays_facts_not_decayed(self, life_dir):
        """Fact with last_seen = today → unchanged."""
        make_items(life_dir, "Projects", "pi-cluster", [
            {"fact": "Fresh fact", "confidence": "single", "last_seen": TODAY, "mentions": 1, "date": TODAY}
        ])
        run_decay(life_dir)
        items = read_items(life_dir, "Projects", "pi-cluster")
        assert items[0]["confidence"] == "single"


# ── Edge Cases ──────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_no_last_seen_uses_date_fallback(self, life_dir):
        """Legacy fact without last_seen uses date field."""
        old_date = str(date.fromisoformat(TODAY) - timedelta(days=20))
        make_items(life_dir, "Projects", "pi-cluster", [
            {"fact": "Legacy fact", "confidence": "single", "date": old_date, "mentions": 1}
        ])
        run_decay(life_dir)
        items = read_items(life_dir, "Projects", "pi-cluster")
        assert items[0]["confidence"] == "stale"

    def test_invalid_date_format(self, life_dir):
        """Fact with bad date → skipped, no crash."""
        make_items(life_dir, "Projects", "pi-cluster", [
            {"fact": "Bad date", "confidence": "single", "last_seen": "bad-date", "mentions": 1, "date": "bad"}
        ])
        result = run_decay(life_dir)
        assert result.returncode == 0

    def test_empty_items_array(self, life_dir):
        """items.json with [] → no error, 0 decayed."""
        make_items(life_dir, "Projects", "pi-cluster", [])
        result = run_decay(life_dir)
        assert result.returncode == 0
        assert "Total: 0" in result.stdout

    def test_items_json_missing(self, life_dir):
        """Entity dir exists but no items.json → skipped."""
        (life_dir / "Projects" / "ghost").mkdir(parents=True)
        result = run_decay(life_dir)
        assert result.returncode == 0


# ── Operations ──────────────────────────────────────────────────────────────


class TestOperations:
    def test_dry_run(self, life_dir):
        """--dry-run reports count but doesn't modify files."""
        old_date = str(date.fromisoformat(TODAY) - timedelta(days=20))
        make_items(life_dir, "Projects", "pi-cluster", [
            {"fact": "Dry test", "confidence": "single", "last_seen": old_date, "mentions": 1, "date": old_date}
        ])
        result = run_decay(life_dir, extra_args=["--dry-run"])
        assert result.returncode == 0
        assert "(dry)" in result.stdout
        # File should NOT be modified
        items = read_items(life_dir, "Projects", "pi-cluster")
        assert items[0]["confidence"] == "single"

    def test_backfill_adds_last_seen(self, life_dir):
        """--backfill adds last_seen = date to facts missing it."""
        make_items(life_dir, "Projects", "pi-cluster", [
            {"fact": "No last_seen", "confidence": "single", "date": "2026-03-15", "mentions": 1}
        ])
        run_decay(life_dir, extra_args=["--backfill"])
        items = read_items(life_dir, "Projects", "pi-cluster")
        assert items[0]["last_seen"] == "2026-03-15"

    def test_backfill_idempotent(self, life_dir):
        """Running backfill twice doesn't change already-set last_seen."""
        make_items(life_dir, "Projects", "pi-cluster", [
            {"fact": "Has last_seen", "confidence": "single", "date": "2026-03-15", "last_seen": "2026-04-01", "mentions": 1}
        ])
        run_decay(life_dir, extra_args=["--backfill"])
        items = read_items(life_dir, "Projects", "pi-cluster")
        assert items[0]["last_seen"] == "2026-04-01"  # Not overwritten

    def test_iterates_all_entity_dirs(self, life_dir):
        """Facts in Projects + People + Companies all processed."""
        old_date = str(date.fromisoformat(TODAY) - timedelta(days=20))
        fact = {"fact": "Test", "confidence": "single", "last_seen": old_date, "mentions": 1, "date": old_date}
        make_items(life_dir, "Projects", "pi-cluster", [fact.copy()])
        make_items(life_dir, "People", "archie", [fact.copy()])
        make_items(life_dir, "Companies", "openrouter", [fact.copy()])
        result = run_decay(life_dir)
        assert result.returncode == 0
        assert "Total: 3" in result.stdout


# ── Temporal Decay ─────────────────────────────────────────────────────────


class TestTemporalDecay:
    def test_temporal_stale_after_7_days(self, life_dir):
        """Temporal fact, last_seen 8 days ago → stale."""
        old_date = str(date.fromisoformat(TODAY) - timedelta(days=8))
        make_items(life_dir, "Projects", "pi-cluster", [
            {"fact": "Deploy in progress", "confidence": "single", "last_seen": old_date,
             "mentions": 1, "date": old_date, "temporal": True}
        ])
        run_decay(life_dir)
        items = read_items(life_dir, "Projects", "pi-cluster")
        assert items[0]["confidence"] == "stale"

    def test_temporal_archived_after_14_days(self, life_dir):
        """Temporal fact, last_seen 15 days ago → archived."""
        old_date = str(date.fromisoformat(TODAY) - timedelta(days=15))
        make_items(life_dir, "Projects", "pi-cluster", [
            {"fact": "Blocked on approval", "confidence": "single", "last_seen": old_date,
             "mentions": 1, "date": old_date, "temporal": True}
        ])
        run_decay(life_dir)
        items = read_items(life_dir, "Projects", "pi-cluster")
        assert items[0]["confidence"] == "archived"

    def test_non_temporal_not_stale_at_7_days(self, life_dir):
        """Non-temporal fact, last_seen 8 days ago → stays single (not stale until 14d)."""
        old_date = str(date.fromisoformat(TODAY) - timedelta(days=8))
        make_items(life_dir, "Projects", "pi-cluster", [
            {"fact": "Cluster has 4 nodes", "confidence": "single", "last_seen": old_date,
             "mentions": 1, "date": old_date, "temporal": False}
        ])
        run_decay(life_dir)
        items = read_items(life_dir, "Projects", "pi-cluster")
        assert items[0]["confidence"] == "single"

    def test_temporal_established_stale_after_14_days(self, life_dir):
        """Temporal established fact, last_seen 15 days ago → stale (vs 30d for non-temporal)."""
        old_date = str(date.fromisoformat(TODAY) - timedelta(days=15))
        make_items(life_dir, "Projects", "pi-cluster", [
            {"fact": "Current config value", "confidence": "established", "last_seen": old_date,
             "mentions": 5, "date": old_date, "temporal": True}
        ])
        run_decay(life_dir)
        items = read_items(life_dir, "Projects", "pi-cluster")
        assert items[0]["confidence"] == "stale"


# ── Temporal Backfill ──────────────────────────────────────────────────────


class TestTemporalBackfill:
    def test_backfill_temporal_adds_false(self, life_dir):
        """--backfill-temporal adds temporal: false to facts missing it."""
        make_items(life_dir, "Projects", "pi-cluster", [
            {"fact": "No temporal field", "confidence": "single", "date": "2026-03-15",
             "last_seen": "2026-03-15", "mentions": 1}
        ])
        run_decay(life_dir, extra_args=["--backfill-temporal"])
        items = read_items(life_dir, "Projects", "pi-cluster")
        assert items[0]["temporal"] is False

    def test_backfill_temporal_preserves_existing(self, life_dir):
        """--backfill-temporal does not overwrite existing temporal: true."""
        make_items(life_dir, "Projects", "pi-cluster", [
            {"fact": "Has temporal", "confidence": "single", "date": "2026-03-15",
             "last_seen": "2026-03-15", "mentions": 1, "temporal": True}
        ])
        run_decay(life_dir, extra_args=["--backfill-temporal"])
        items = read_items(life_dir, "Projects", "pi-cluster")
        assert items[0]["temporal"] is True
