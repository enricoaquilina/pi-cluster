import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

APPLY_SCRIPT = Path(__file__).parent.parent / "apply_extraction.py"
SESSION_SCRIPT = Path(__file__).parent.parent / "session_archive.py"
DEDUP_SCRIPT = Path(__file__).parent.parent / "dedup_skills.py"
TODAY = "2026-03-30"


@pytest.fixture
def life_dir(tmp_path):
    """~/life/ tree for Phase 4 testing."""
    (tmp_path / "Projects/test-proj").mkdir(parents=True)
    (tmp_path / "Projects/test-proj/items.json").write_text("[]")
    (tmp_path / "Projects/test-proj/summary.md").write_text("---\ntype: project\n---\n")
    (tmp_path / "Projects/_template").mkdir()
    (tmp_path / "People").mkdir()
    (tmp_path / "Companies").mkdir()
    (tmp_path / "Areas/about-me").mkdir(parents=True)
    for f in ["hard-rules", "workflow-habits", "communication-preferences", "lessons-learned"]:
        (tmp_path / f"Areas/about-me/{f}.md").write_text(f"# {f}\n")
    (tmp_path / "Daily/2026/03").mkdir(parents=True)
    (tmp_path / f"Daily/2026/03/{TODAY}.md").write_text(
        f"---\ndate: {TODAY}\n---\n\n_Not yet consolidated_"
    )
    (tmp_path / "Resources/skills").mkdir(parents=True)
    (tmp_path / "logs").mkdir()
    return tmp_path


def run_script(script, life_dir, args=None, stdin_data=None, env_extra=None):
    env = os.environ.copy()
    env["LIFE_DIR"] = str(life_dir)
    env["CONSOLIDATION_DATE"] = TODAY
    if env_extra:
        env.update(env_extra)
    cmd = [sys.executable, str(script)]
    if args:
        cmd.extend(args)
    return subprocess.run(cmd, input=stdin_data, capture_output=True, text=True, env=env, timeout=30)


def empty_payload(**overrides):
    base = {"new_entities": [], "fact_updates": [], "tacit_knowledge": [], "summary": ""}
    base.update(overrides)
    return base


class TestConfidenceScoring:
    def test_new_fact_starts_as_single(self, life_dir):
        payload = empty_payload(fact_updates=[{
            "entity_type": "project", "entity": "test-proj",
            "date": TODAY, "fact": "Server runs on port 8080",
            "category": "configuration"
        }])
        run_script(APPLY_SCRIPT, life_dir, stdin_data=json.dumps(payload))
        items = json.loads((life_dir / "Projects/test-proj/items.json").read_text())
        assert items[0]["confidence"] == "single"
        assert items[0]["mentions"] == 1

    def test_duplicate_reinforces_to_confirmed(self, life_dir):
        payload = empty_payload(fact_updates=[{
            "entity_type": "project", "entity": "test-proj",
            "date": TODAY, "fact": "Server runs on port 8080",
            "category": "configuration"
        }])
        run_script(APPLY_SCRIPT, life_dir, stdin_data=json.dumps(payload))
        run_script(APPLY_SCRIPT, life_dir, stdin_data=json.dumps(payload))
        items = json.loads((life_dir / "Projects/test-proj/items.json").read_text())
        assert len(items) == 1  # not duplicated
        assert items[0]["mentions"] == 2
        assert items[0]["confidence"] == "confirmed"

    def test_four_mentions_becomes_established(self, life_dir):
        payload = empty_payload(fact_updates=[{
            "entity_type": "project", "entity": "test-proj",
            "date": TODAY, "fact": "Always restart after deploy",
            "category": "lesson"
        }])
        for _ in range(4):
            run_script(APPLY_SCRIPT, life_dir, stdin_data=json.dumps(payload))
        items = json.loads((life_dir / "Projects/test-proj/items.json").read_text())
        assert len(items) == 1
        assert items[0]["mentions"] == 4
        assert items[0]["confidence"] == "established"


class TestSessionArchive:
    def test_creates_archive_file(self, life_dir):
        """Create mock sessions and verify archive is written."""
        sessions_dir = life_dir / ".claude-sessions"
        sessions_dir.mkdir()
        today_ts = int(date.fromisoformat(TODAY).strftime("%s")) * 1000 + 36000000  # 10 AM
        (sessions_dir / "12345.json").write_text(json.dumps({
            "pid": 12345,
            "sessionId": "test-uuid-1",
            "cwd": "/home/enrico",
            "startedAt": today_ts
        }))

        r = run_script(SESSION_SCRIPT, life_dir, env_extra={"SESSIONS_DIR_OVERRIDE": str(sessions_dir)})
        assert r.returncode == 0
        # Check archive was created
        archive_path = life_dir / "Daily/2026/03" / f"sessions-{TODAY}.json"
        assert archive_path.exists()
        archive = json.loads(archive_path.read_text())
        assert archive["count"] == 1
        assert archive["sessions"][0]["session_id"] == "test-uuid-1"

    def test_handles_no_sessions(self, life_dir):
        empty_dir = life_dir / ".empty-sessions"
        # Don't create the dir — script should handle missing dir gracefully
        r = run_script(SESSION_SCRIPT, life_dir, env_extra={"SESSIONS_DIR_OVERRIDE": str(empty_dir)})
        assert r.returncode == 0
        assert "no sessions" in r.stdout.lower() or r.stdout == ""

    def test_session_near_midnight_utc_included(self, life_dir):
        """Session started just past midnight UTC (but same local day) should be included."""
        from datetime import datetime, timezone
        sessions_dir = life_dir / ".claude-sessions"
        sessions_dir.mkdir()
        # Create a session at 00:30 UTC on TODAY — in CET/CEST this is still TODAY
        today_date = date.fromisoformat(TODAY)
        utc_midnight = datetime(today_date.year, today_date.month, today_date.day, 0, 30, tzinfo=timezone.utc)
        local_date = utc_midnight.astimezone().date()
        # Only run the assertion if local timezone puts this on the same day
        # (Skip if running in UTC where there's no difference)
        if local_date == today_date:
            ts_ms = int(utc_midnight.timestamp() * 1000)
            (sessions_dir / "99999.json").write_text(json.dumps({
                "pid": 99999,
                "sessionId": "midnight-session",
                "cwd": "/home/enrico",
                "startedAt": ts_ms
            }))
            r = run_script(SESSION_SCRIPT, life_dir, env_extra={"SESSIONS_DIR_OVERRIDE": str(sessions_dir)})
            assert r.returncode == 0
            archive_path = life_dir / "Daily/2026/03" / f"sessions-{TODAY}.json"
            assert archive_path.exists()
            archive = json.loads(archive_path.read_text())
            assert any(s["session_id"] == "midnight-session" for s in archive["sessions"])


class TestSkillDedup:
    def test_detects_similar_skills(self, life_dir):
        """Two skills with overlapping steps should be flagged."""
        (life_dir / "Resources/skills/deploy-app.md").write_text(
            "---\ntype: skill\nname: Deploy App\n---\n\n## Steps\n"
            "1. Stop the service\n2. Pull latest code\n3. Build docker image\n4. Start the service\n"
        )
        (life_dir / "Resources/skills/deploy-backend.md").write_text(
            "---\ntype: skill\nname: Deploy Backend\n---\n\n## Steps\n"
            "1. Stop the service\n2. Pull latest code\n3. Build docker image\n4. Run migrations\n5. Start the service\n"
        )
        r = run_script(DEDUP_SCRIPT, life_dir)
        assert r.returncode == 0
        assert "1 potential duplicate" in r.stdout or "duplicates found" in r.stdout
        report = life_dir / "logs/skill-dedup-report.md"
        assert report.exists()
        content = report.read_text()
        assert "deploy-app" in content
        assert "deploy-backend" in content

    def test_no_duplicates_with_different_skills(self, life_dir):
        (life_dir / "Resources/skills/rotate-keys.md").write_text(
            "---\ntype: skill\n---\n\n## Steps\n1. Generate new API key\n2. Update .env file\n3. Restart service\n"
        )
        (life_dir / "Resources/skills/backup-db.md").write_text(
            "---\ntype: skill\n---\n\n## Steps\n1. Run pg_dump\n2. Compress archive\n3. Upload to S3\n"
        )
        r = run_script(DEDUP_SCRIPT, life_dir)
        assert r.returncode == 0
        assert "no duplicate" in r.stdout.lower()

    def test_empty_skills_dir(self, life_dir):
        r = run_script(DEDUP_SCRIPT, life_dir)
        assert r.returncode == 0

    def test_dry_run(self, life_dir):
        (life_dir / "Resources/skills/skill-a.md").write_text(
            "---\n---\n\n## Steps\n1. Do thing\n2. Do other thing\n"
        )
        (life_dir / "Resources/skills/skill-b.md").write_text(
            "---\n---\n\n## Steps\n1. Do thing\n2. Do other thing\n"
        )
        r = run_script(DEDUP_SCRIPT, life_dir, args=["--dry-run"])
        assert r.returncode == 0
        assert not (life_dir / "logs/skill-dedup-report.md").exists()
