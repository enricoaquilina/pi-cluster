import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

APPLY_SCRIPT = Path(__file__).parent.parent / "apply_extraction.py"
ARCHIVE_SCRIPT = Path(__file__).parent.parent / "auto_archive.py"
WEEKLY_SCRIPT = Path(__file__).parent.parent / "weekly_summary.py"
CARRY_SCRIPT = Path(__file__).parent.parent / "carry_forward.py"
TODAY = "2026-03-30"


@pytest.fixture
def life_dir(tmp_path):
    """Full ~/life/ tree for Phase 3 testing."""
    # Projects
    (tmp_path / "Projects/active-proj").mkdir(parents=True)
    (tmp_path / "Projects/active-proj/summary.md").write_text(
        "---\ntype: project\nname: Active\nstatus: active\n---\nStill working.\n"
    )
    (tmp_path / "Projects/active-proj/items.json").write_text("[]")

    (tmp_path / "Projects/done-proj").mkdir(parents=True)
    (tmp_path / "Projects/done-proj/summary.md").write_text(
        "---\ntype: project\nname: Done Project\nstatus: completed\n---\nFinished.\n"
    )
    (tmp_path / "Projects/done-proj/items.json").write_text("[]")

    (tmp_path / "Projects/_template").mkdir(parents=True)

    # Archives (empty)
    (tmp_path / "Archives").mkdir(parents=True)

    # People / Companies
    (tmp_path / "People").mkdir()
    (tmp_path / "Companies").mkdir()

    # Areas
    (tmp_path / "Areas/about-me").mkdir(parents=True)
    for f in ["hard-rules", "workflow-habits", "communication-preferences", "lessons-learned"]:
        (tmp_path / f"Areas/about-me/{f}.md").write_text(f"# {f}\n")

    # Daily notes for the past week
    for i in range(7):
        d = date(2026, 3, 30) - timedelta(days=i)
        note_dir = tmp_path / "Daily" / str(d.year) / f"{d.month:02d}"
        note_dir.mkdir(parents=True, exist_ok=True)

        if i == 0:  # today
            content = f"---\ndate: {d}\n---\n\n## What We Worked On\n- Built memory system\n\n## Decisions Made\n- Use 3-layer PARA\n\n## Pending Items\n- [ ]\n\n## Active Projects\n- **pi-cluster**: stable\n\n## New Facts Learned\n- QMD supports BM25 + vector\n\n## Consolidation\n_Not yet consolidated_\n"
        elif i == 1:  # yesterday
            content = f"---\ndate: {d}\n---\n\n## What We Worked On\n- Planned the system\n\n## Decisions Made\n\n## Pending Items\n- [ ] Install QMD search layer\n- [ ] Update MC documentation\n- [x] Fix crontab entries\n\n## Active Projects\n- **openclaw-maxwell**: planning phase\n\n## New Facts Learned\n\n## Consolidation\n_Not yet consolidated_\n"
        else:
            content = f"---\ndate: {d}\n---\n\n## What We Worked On\n\n## Decisions Made\n\n## Pending Items\n- [ ]\n\n## Active Projects\n\n## New Facts Learned\n\n## Consolidation\n_Not yet consolidated_\n"

        (note_dir / f"{d}.md").write_text(content)

    return tmp_path


def run_script(script, life_dir, args=None, env_overrides=None, stdin_data=None):
    env = os.environ.copy()
    env["LIFE_DIR"] = str(life_dir)
    env["CONSOLIDATION_DATE"] = TODAY
    if env_overrides:
        env.update(env_overrides)
    cmd = [sys.executable, str(script)]
    if args:
        cmd.extend(args)
    return subprocess.run(
        cmd,
        input=stdin_data,
        capture_output=True, text=True, env=env, timeout=30,
    )


class TestContradictionDetection:
    def test_flags_contradiction_via_supersedes(self, life_dir):
        """New fact with explicit supersedes field marks old fact as superseded (Haiku-delegated)."""
        # First: add an initial fact
        payload1 = {
            "new_entities": [], "fact_updates": [{
                "entity_type": "project", "entity": "active-proj",
                "date": "2026-03-28", "fact": "Order size is $5.54 per trade",
                "category": "configuration"
            }], "tacit_knowledge": [], "summary": ""
        }
        run_script(APPLY_SCRIPT, life_dir, stdin_data=json.dumps(payload1))

        # Second: add contradicting fact with explicit supersedes (as Haiku would produce)
        payload2 = {
            "new_entities": [], "fact_updates": [{
                "entity_type": "project", "entity": "active-proj",
                "date": TODAY, "fact": "Order size reduced to $4.00 per trade",
                "category": "configuration",
                "supersedes": "Order size is $5.54 per trade"
            }], "tacit_knowledge": [], "summary": ""
        }
        r = run_script(APPLY_SCRIPT, life_dir, stdin_data=json.dumps(payload2))

        items = json.loads((life_dir / "Projects/active-proj/items.json").read_text())
        assert len(items) == 2
        # Old fact should be marked as superseded
        old = [i for i in items if "$5.54" in i["fact"]][0]
        assert old["confidence"] == "superseded"
        assert old["superseded_by"] == "Order size reduced to $4.00 per trade"
        assert "SUPERSEDED" in r.stderr

    def test_no_contradiction_different_category(self, life_dir):
        """Facts in different categories should not flag contradiction."""
        payload1 = {
            "new_entities": [], "fact_updates": [{
                "entity_type": "project", "entity": "active-proj",
                "date": "2026-03-28", "fact": "Deployed v2",
                "category": "deployment"
            }], "tacit_knowledge": [], "summary": ""
        }
        run_script(APPLY_SCRIPT, life_dir, stdin_data=json.dumps(payload1))

        payload2 = {
            "new_entities": [], "fact_updates": [{
                "entity_type": "project", "entity": "active-proj",
                "date": TODAY, "fact": "Changed config to use port 8080",
                "category": "configuration"
            }], "tacit_knowledge": [], "summary": ""
        }
        run_script(APPLY_SCRIPT, life_dir, stdin_data=json.dumps(payload2))

        items = json.loads((life_dir / "Projects/active-proj/items.json").read_text())
        assert len(items) == 2
        assert "supersedes" not in items[1]


class TestAutoArchive:
    def test_archives_completed_project(self, life_dir):
        r = run_script(ARCHIVE_SCRIPT, life_dir)
        assert r.returncode == 0
        assert (life_dir / "Archives/Projects/done-proj/summary.md").exists()
        assert not (life_dir / "Projects/done-proj").exists()

    def test_keeps_active_project(self, life_dir):
        run_script(ARCHIVE_SCRIPT, life_dir)
        assert (life_dir / "Projects/active-proj").exists()

    def test_skips_template(self, life_dir):
        run_script(ARCHIVE_SCRIPT, life_dir)
        assert (life_dir / "Projects/_template").exists()

    def test_idempotent(self, life_dir):
        run_script(ARCHIVE_SCRIPT, life_dir)
        # Run again — should skip already-archived
        r = run_script(ARCHIVE_SCRIPT, life_dir)
        assert "already archived" not in r.stdout  # done-proj is gone from Projects/

    def test_dry_run(self, life_dir):
        r = run_script(ARCHIVE_SCRIPT, life_dir, args=["--dry-run"])
        assert r.returncode == 0
        assert (life_dir / "Projects/done-proj").exists()  # not moved
        assert "(dry)" in r.stdout


class TestWeeklySummary:
    def test_generates_summary(self, life_dir):
        r = run_script(WEEKLY_SCRIPT, life_dir)
        assert r.returncode == 0
        # Find the output file
        summary_files = list((life_dir / "Daily/2026").glob("W*-summary.md"))
        assert len(summary_files) == 1
        content = summary_files[0].read_text()
        assert "Weekly Summary" in content
        assert "Built memory system" in content

    def test_includes_pending_items(self, life_dir):
        run_script(WEEKLY_SCRIPT, life_dir)
        summary_files = list((life_dir / "Daily/2026").glob("W*-summary.md"))
        content = summary_files[0].read_text()
        assert "Install QMD" in content

    def test_includes_decisions(self, life_dir):
        run_script(WEEKLY_SCRIPT, life_dir)
        summary_files = list((life_dir / "Daily/2026").glob("W*-summary.md"))
        content = summary_files[0].read_text()
        assert "3-layer PARA" in content

    def test_dry_run(self, life_dir):
        r = run_script(WEEKLY_SCRIPT, life_dir, args=["--dry-run"])
        assert r.returncode == 0
        summary_files = list((life_dir / "Daily/2026").glob("W*-summary.md"))
        assert len(summary_files) == 0  # not written

    def test_iso_week_year_boundary(self, tmp_path):
        """Jan 1 2027 is ISO week 53 of 2026 — summary should use 2026, not 2027."""
        # Jan 1 2027 is a Friday → ISO week 53 of 2026
        boundary_date = "2027-01-01"
        # Create minimal daily note for this date
        (tmp_path / "Daily/2027/01").mkdir(parents=True)
        (tmp_path / f"Daily/2027/01/{boundary_date}.md").write_text(
            f"---\ndate: {boundary_date}\n---\n\n## What We Worked On\n- Year boundary test\n"
        )
        env = os.environ.copy()
        env["LIFE_DIR"] = str(tmp_path)
        env["CONSOLIDATION_DATE"] = boundary_date
        r = subprocess.run(
            [sys.executable, str(WEEKLY_SCRIPT)],
            capture_output=True, env=env, text=True,
        )
        assert r.returncode == 0
        # Should be filed under 2026 (ISO year), not 2027 (Gregorian year)
        assert (tmp_path / "Daily/2026/W53-summary.md").exists()
        assert not (tmp_path / "Daily/2027/W53-summary.md").exists()


class TestCarryForward:
    def test_carries_open_items(self, life_dir):
        r = run_script(CARRY_SCRIPT, life_dir,
                       env_overrides={"CONSOLIDATION_DATE": "2026-03-30"})
        assert r.returncode == 0
        today_content = (life_dir / "Daily/2026/03/2026-03-30.md").read_text()
        assert "Install QMD search layer" in today_content
        assert "Update MC documentation" in today_content

    def test_does_not_carry_checked_items(self, life_dir):
        run_script(CARRY_SCRIPT, life_dir)
        today_content = (life_dir / "Daily/2026/03/2026-03-30.md").read_text()
        assert "Fix crontab entries" not in today_content  # was [x] checked

    def test_idempotent(self, life_dir):
        run_script(CARRY_SCRIPT, life_dir)
        run_script(CARRY_SCRIPT, life_dir)
        today_content = (life_dir / "Daily/2026/03/2026-03-30.md").read_text()
        # Should only appear once
        assert today_content.count("Install QMD search layer") == 1

    def test_no_yesterday_note(self, life_dir):
        (life_dir / "Daily/2026/03/2026-03-29.md").unlink()
        r = run_script(CARRY_SCRIPT, life_dir)
        assert r.returncode == 0
        assert "nothing to carry" in r.stdout.lower() or "no yesterday" in r.stdout.lower()
