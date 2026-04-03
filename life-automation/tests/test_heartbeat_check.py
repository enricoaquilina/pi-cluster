import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "heartbeat_check.py"


@pytest.fixture
def life_dir(tmp_path):
    (tmp_path / "logs").mkdir()
    # Create 5 days of daily notes
    for i in range(5):
        d = date(2026, 3, 30) - timedelta(days=i)
        note_dir = tmp_path / "Daily" / str(d.year) / f"{d.month:02d}"
        note_dir.mkdir(parents=True, exist_ok=True)
        if i <= 2:  # last 3 days: pi-cluster blocked
            content = f"---\ndate: {d}\n---\n\n## What We Worked On\n- stuff\n\n## Decisions Made\n\n## Pending\n- [ ]\n\n## Active Projects\n- **[[pi-cluster]]**: Phase 1b blocked on vault vars\n- **[[openclaw-maxwell]]**: operational\n\n## New Facts Learned\n\n## Consolidation\n_Not yet consolidated_\n"
        else:
            content = f"---\ndate: {d}\n---\n\n## What We Worked On\n\n## Decisions Made\n\n## Pending\n- [ ]\n\n## Active Projects\n- **[[pi-cluster]]**: in progress\n- **[[openclaw-maxwell]]**: in progress\n\n## New Facts Learned\n\n## Consolidation\n_Not yet consolidated_\n"
        (note_dir / f"{d}.md").write_text(content)
    return tmp_path


def run(life_dir, args=None, today="2026-03-30"):
    env = os.environ.copy()
    env["LIFE_DIR"] = str(life_dir)
    env["CONSOLIDATION_DATE"] = today
    cmd = [sys.executable, str(SCRIPT)]
    if args:
        cmd.extend(args)
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)


class TestParseActiveProjects:
    def test_parses_wiki_link_projects(self, life_dir):
        r = run(life_dir, ["--json", "--dry-run"])
        assert r.returncode == 0
        data = json.loads(r.stdout)
        slugs = [p["slug"] for p in data["projects"]]
        assert "pi-cluster" in slugs
        assert "openclaw-maxwell" in slugs

    def test_no_active_projects_section(self, life_dir):
        # Overwrite today's note with no Active Projects
        d = date(2026, 3, 30)
        note = life_dir / "Daily/2026/03/2026-03-30.md"
        note.write_text(f"---\ndate: {d}\n---\n\n## What We Worked On\n\n## Pending\n- [ ]\n")
        r = run(life_dir, ["--json", "--dry-run"])
        data = json.loads(r.stdout)
        assert data["projects"] == []

    def test_no_daily_note(self, life_dir):
        (life_dir / "Daily/2026/03/2026-03-30.md").unlink()
        r = run(life_dir, ["--json", "--dry-run"])
        data = json.loads(r.stdout)
        assert data["projects"] == []


class TestDetectFlags:
    def test_blocked_flag(self, life_dir):
        r = run(life_dir, ["--json", "--dry-run"])
        data = json.loads(r.stdout)
        pi = next(p for p in data["projects"] if p["slug"] == "pi-cluster")
        assert "blocked" in pi["flags"]

    def test_stable_flag(self, life_dir):
        r = run(life_dir, ["--json", "--dry-run"])
        data = json.loads(r.stdout)
        om = next(p for p in data["projects"] if p["slug"] == "openclaw-maxwell")
        assert "stable" in om["flags"]


class TestStalenessAndBlocked:
    def test_staleness_zero_for_today(self, life_dir):
        r = run(life_dir, ["--json", "--dry-run"])
        data = json.loads(r.stdout)
        pi = next(p for p in data["projects"] if p["slug"] == "pi-cluster")
        assert pi["staleness_days"] == 0

    def test_blocked_days_three(self, life_dir):
        r = run(life_dir, ["--json", "--dry-run"])
        data = json.loads(r.stdout)
        pi = next(p for p in data["projects"] if p["slug"] == "pi-cluster")
        assert pi["blocked_days"] == 3

    def test_blocked_needs_attention(self, life_dir):
        r = run(life_dir, ["--json", "--dry-run"])
        data = json.loads(r.stdout)
        pi = next(p for p in data["projects"] if p["slug"] == "pi-cluster")
        assert pi["needs_attention"] is True
        assert "3 consecutive days" in pi["attention_reason"]

    def test_stable_no_attention(self, life_dir):
        r = run(life_dir, ["--json", "--dry-run"])
        data = json.loads(r.stdout)
        om = next(p for p in data["projects"] if p["slug"] == "openclaw-maxwell")
        assert om["needs_attention"] is False


class TestEscalation:
    def test_escalation_created_for_blocked_3_days(self, life_dir):
        r = run(life_dir, ["--json"])  # NOT dry-run
        data = json.loads(r.stdout)
        assert len(data["escalations"]) == 1
        assert data["escalations"][0]["slug"] == "pi-cluster"
        # Check pending item was added to daily note
        content = (life_dir / "Daily/2026/03/2026-03-30.md").read_text()
        assert "ESCALATION: [[pi-cluster]]" in content

    def test_escalation_idempotent(self, life_dir):
        run(life_dir)
        run(life_dir)
        content = (life_dir / "Daily/2026/03/2026-03-30.md").read_text()
        assert content.count("ESCALATION: [[pi-cluster]]") == 1

    def test_no_escalation_under_3_days(self, life_dir):
        # Remove day -2 note so only 2 consecutive blocked days
        (life_dir / "Daily/2026/03/2026-03-28.md").unlink()
        r = run(life_dir, ["--json", "--dry-run"])
        data = json.loads(r.stdout)
        assert len(data["escalations"]) == 0


class TestLogging:
    def test_creates_heartbeat_log(self, life_dir):
        run(life_dir)
        log_path = life_dir / "logs" / "heartbeat.json"
        assert log_path.exists()
        entries = json.loads(log_path.read_text())
        assert len(entries) == 1

    def test_appends_to_heartbeat_log(self, life_dir):
        run(life_dir)
        run(life_dir)
        entries = json.loads((life_dir / "logs/heartbeat.json").read_text())
        assert len(entries) == 2
