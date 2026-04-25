"""End-to-end tests for the nightly consolidation pipeline.

Tests the full flow: extraction payload → apply_extraction --stage →
review auto-graduate → facts land in PARA locations (items.json, lessons-learned.md, skills/).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

CANONICAL = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
MINI_LIFE_SRC = FIXTURES / "mini-life"


@pytest.fixture
def pipeline_life(tmp_path: Path) -> Path:
    """Extended mini-life with Areas/about-me and Resources/skills for PARA testing."""
    dst = tmp_path / "life"
    shutil.copytree(MINI_LIFE_SRC, dst)
    # PARA: Areas
    about_me = dst / "Areas" / "about-me"
    about_me.mkdir(parents=True)
    (about_me / "lessons-learned.md").write_text("# Lessons Learned\n")
    (about_me / "workflow-habits.md").write_text("# Workflow Habits\n")
    (about_me / "hard-rules.md").write_text("# Hard Rules\n")
    (about_me / "communication-preferences.md").write_text("# Communication Preferences\n")
    # PARA: Resources
    skills_dir = dst / "Resources" / "skills"
    skills_dir.mkdir(parents=True)
    # Logs
    logs_dir = dst / "logs"
    logs_dir.mkdir(exist_ok=True)
    return dst


def run_apply(life_dir: Path, payload: dict, *, stage: bool = False) -> subprocess.CompletedProcess:
    args = [sys.executable, str(CANONICAL / "apply_extraction.py")]
    if stage:
        args.append("--stage")
    env = {
        **os.environ,
        "LIFE_DIR": str(life_dir),
        "CONSOLIDATION_DATE": "2026-04-24",
        "PYTHONHASHSEED": "random",
        "TZ": "UTC",
    }
    return subprocess.run(
        args, input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=30,
    )


def run_review(life_dir: Path, *args: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "LIFE_DIR": str(life_dir),
        "PYTHONHASHSEED": "random",
        "TZ": "UTC",
    }
    return subprocess.run(
        [sys.executable, str(CANONICAL / "review.py"), *args],
        capture_output=True, text=True, env=env, timeout=30,
    )


REALISTIC_PAYLOAD = {
    "new_entities": [],
    "fact_updates": [
        {
            "entity_type": "project",
            "entity": "gadget",
            "date": "2026-04-24",
            "fact": "Deployed v2.3 with WebSocket support",
            "category": "deployment",
            "temporal": False,
        },
        {
            "entity_type": "project",
            "entity": "gadget",
            "date": "2026-04-24",
            "fact": "Switched from polling to event-driven architecture",
            "category": "decision",
            "temporal": False,
        },
        {
            "entity_type": "project",
            "entity": "gadget",
            "date": "2026-04-24",
            "fact": "Currently running load test at 500 req/s",
            "category": "event",
            "temporal": True,
        },
    ],
    "tacit_knowledge": [
        {
            "file": "lessons-learned",
            "entry": "WebSocket connections need explicit ping/pong frames to survive NAT timeouts.",
        },
    ],
    "skills": [
        {
            "name": "deploy-gadget",
            "display": "How to Deploy Gadget",
            "steps": ["Run tests", "Build Docker image", "Push to registry", "Restart service"],
        },
    ],
    "relationships": [],
    "summary": "Deployed gadget v2.3, switched to event-driven, running load tests.",
}


class TestFullPipeline:
    """E2E: extraction → staging → graduation → PARA locations."""

    def test_stage_then_graduate_deployment(self, pipeline_life: Path):
        """Deployment facts auto-graduate to items.json."""
        result = run_apply(pipeline_life, REALISTIC_PAYLOAD, stage=True)
        assert result.returncode == 0
        assert "Staged" in result.stdout

        # Candidates should exist
        cand_path = pipeline_life / "logs" / "candidates.jsonl"
        assert cand_path.exists()
        candidates = [json.loads(line) for line in cand_path.read_text().splitlines()]
        assert len(candidates) == 3

        # Auto-graduate
        result = run_review(pipeline_life, "auto-graduate")
        assert result.returncode == 0
        assert "Graduated" in result.stdout

        # Deployment fact should be in items.json
        items = json.loads((pipeline_life / "Projects" / "gadget" / "items.json").read_text())
        deployed_facts = [f for f in items if "v2.3" in f.get("fact", "")]
        assert len(deployed_facts) == 1
        assert deployed_facts[0]["category"] == "deployment"

    def test_decision_fact_needs_review(self, pipeline_life: Path):
        """Decision facts get staged but require human review."""
        payload = {
            "new_entities": [],
            "fact_updates": [
                {
                    "entity_type": "project",
                    "entity": "gadget",
                    "date": "2026-04-24",
                    "fact": "Decided to migrate from SQLite to PostgreSQL",
                    "category": "decision",
                    "temporal": False,
                },
            ],
            "tacit_knowledge": [],
            "skills": [],
            "relationships": [],
            "summary": "DB migration decision.",
        }
        run_apply(pipeline_life, payload, stage=True)

        cand_path = pipeline_life / "logs" / "candidates.jsonl"
        candidates = [json.loads(line) for line in cand_path.read_text().splitlines()]
        decision_cands = [c for c in candidates if c["category"] == "decision"]
        assert len(decision_cands) == 1
        assert decision_cands[0]["needs_review"] is True

    def test_temporal_flag_preserved(self, pipeline_life: Path):
        """Temporal facts retain their flag through staging and graduation."""
        run_apply(pipeline_life, REALISTIC_PAYLOAD, stage=True)
        run_review(pipeline_life, "auto-graduate")

        items = json.loads((pipeline_life / "Projects" / "gadget" / "items.json").read_text())
        load_test = [f for f in items if "load test" in f.get("fact", "")]
        assert len(load_test) == 1
        assert load_test[0].get("temporal") is True

        deployed = [f for f in items if "v2.3" in f.get("fact", "")]
        assert deployed[0].get("temporal") is False


class TestPARALocations:
    """Facts land in correct PARA structure locations."""

    def test_tacit_knowledge_in_areas(self, pipeline_life: Path):
        """Tacit knowledge writes to Areas/about-me/."""
        run_apply(pipeline_life, REALISTIC_PAYLOAD, stage=True)

        lessons = (pipeline_life / "Areas" / "about-me" / "lessons-learned.md").read_text()
        assert "WebSocket connections need explicit ping/pong" in lessons

    def test_skills_in_resources(self, pipeline_life: Path):
        """Skills create files in Resources/skills/."""
        run_apply(pipeline_life, REALISTIC_PAYLOAD, stage=True)

        skill_path = pipeline_life / "Resources" / "skills" / "deploy-gadget.md"
        assert skill_path.exists()
        content = skill_path.read_text()
        assert "How to Deploy Gadget" in content
        assert "Build Docker image" in content

    def test_facts_in_projects(self, pipeline_life: Path):
        """Facts land in Projects/*/items.json."""
        run_apply(pipeline_life, REALISTIC_PAYLOAD, stage=True)
        run_review(pipeline_life, "auto-graduate")

        items_path = pipeline_life / "Projects" / "gadget" / "items.json"
        items = json.loads(items_path.read_text())
        new_facts = [f for f in items if f.get("date") == "2026-04-24"]
        assert len(new_facts) >= 1

    def test_new_entity_creates_para_folders(self, pipeline_life: Path):
        """New entities create correct PARA folder type."""
        payload = {
            "new_entities": [
                {"type": "project", "name": "new-proj", "display": "New Project"},
                {"type": "person", "name": "jane", "display": "Jane Doe"},
                {"type": "company", "name": "acme-corp", "display": "ACME Corp"},
            ],
            "fact_updates": [],
            "tacit_knowledge": [],
            "skills": [],
            "relationships": [],
            "summary": "New entities.",
        }
        run_apply(pipeline_life, payload, stage=True)

        assert (pipeline_life / "Projects" / "new-proj" / "summary.md").exists()
        assert (pipeline_life / "People" / "jane" / "summary.md").exists()
        assert (pipeline_life / "Companies" / "acme-corp" / "items.json").exists()


class TestReviewQueue:
    """Review queue generation for human review."""

    def test_queue_generated_after_graduation(self, pipeline_life: Path):
        run_apply(pipeline_life, REALISTIC_PAYLOAD, stage=True)
        run_review(pipeline_life, "auto-graduate")
        result = run_review(pipeline_life, "queue")
        assert result.returncode == 0

        queue_path = pipeline_life / "REVIEW_QUEUE.md"
        assert queue_path.exists()

    def test_manual_graduate(self, pipeline_life: Path):
        """Manual graduation with rationale."""
        payload = {
            "new_entities": [],
            "fact_updates": [
                {
                    "entity_type": "project",
                    "entity": "gadget",
                    "date": "2026-04-24",
                    "fact": "Lesson: always run smoke tests after deploy",
                    "category": "lesson",
                    "temporal": False,
                },
            ],
            "tacit_knowledge": [],
            "skills": [],
            "relationships": [],
            "summary": "Lesson learned.",
        }
        run_apply(pipeline_life, payload, stage=True)

        cand_path = pipeline_life / "logs" / "candidates.jsonl"
        candidates = [json.loads(line) for line in cand_path.read_text().splitlines()]
        cand_id = candidates[0]["id"]

        result = run_review(pipeline_life, "graduate", cand_id, "verified in testing")
        assert result.returncode == 0

        items = json.loads((pipeline_life / "Projects" / "gadget" / "items.json").read_text())
        graduated = [f for f in items if "smoke tests" in f.get("fact", "")]
        assert len(graduated) == 1

    def test_manual_reject(self, pipeline_life: Path):
        """Rejected candidates stay in candidates.jsonl with rejected status."""
        payload = {
            "new_entities": [],
            "fact_updates": [
                {
                    "entity_type": "project",
                    "entity": "gadget",
                    "date": "2026-04-24",
                    "fact": "Wrong fact about gadget",
                    "category": "event",
                    "temporal": False,
                },
            ],
            "tacit_knowledge": [],
            "skills": [],
            "relationships": [],
            "summary": "Wrong fact.",
        }
        run_apply(pipeline_life, payload, stage=True)

        cand_path = pipeline_life / "logs" / "candidates.jsonl"
        candidates = [json.loads(line) for line in cand_path.read_text().splitlines()]
        cand_id = candidates[0]["id"]

        result = run_review(pipeline_life, "reject", cand_id, "incorrect information")
        assert result.returncode == 0

        candidates = [json.loads(line) for line in cand_path.read_text().splitlines()]
        rejected = [c for c in candidates if c["id"] == cand_id]
        assert rejected[0]["status"] == "rejected"


    def test_notify_with_review_items(self, pipeline_life: Path):
        """Notify outputs Telegram-formatted HTML when items need review."""
        run_apply(pipeline_life, REALISTIC_PAYLOAD, stage=True)
        result = run_review(pipeline_life, "notify")
        assert result.returncode == 0
        assert "Nightly Review Queue" in result.stdout
        assert "need human review" in result.stdout
        assert "gadget" in result.stdout

    def test_notify_empty_exits_nonzero(self, pipeline_life: Path):
        """Notify exits 1 when no pending candidates — shell skips Telegram."""
        result = run_review(pipeline_life, "notify")
        assert result.returncode == 1
        assert result.stdout.strip() == ""

    def test_notify_auto_only_still_sends(self, pipeline_life: Path):
        """Notify sends even with only auto-graduatable items (no review needed)."""
        payload = {
            "new_entities": [],
            "fact_updates": [
                {
                    "entity_type": "project",
                    "entity": "gadget",
                    "date": "2026-04-24",
                    "fact": "Deployed v4 with new config",
                    "category": "deployment",
                    "temporal": False,
                },
            ],
            "tacit_knowledge": [],
            "skills": [],
            "relationships": [],
            "summary": "Deploy only.",
        }
        run_apply(pipeline_life, payload, stage=True)
        result = run_review(pipeline_life, "notify")
        assert result.returncode == 0
        assert "auto-graduatable" in result.stdout
        assert "need human review" not in result.stdout


class TestSupersedes:
    """Contradiction detection and supersedes handling."""

    def test_superseded_fact_needs_manual_review(self, pipeline_life: Path):
        """Facts with supersedes require human review (contradicts existing)."""
        payload = {
            "new_entities": [],
            "fact_updates": [
                {
                    "entity_type": "project",
                    "entity": "gadget",
                    "date": "2026-04-24",
                    "fact": "gadget uses PostgreSQL",
                    "category": "configuration",
                    "temporal": False,
                    "supersedes": "gadget uses SQLite",
                },
            ],
            "tacit_knowledge": [],
            "skills": [],
            "relationships": [],
            "summary": "DB migration.",
        }
        run_apply(pipeline_life, payload, stage=True)

        # Auto-graduate should skip it (needs_review=True due to contradiction)
        run_review(pipeline_life, "auto-graduate")
        items = json.loads((pipeline_life / "Projects" / "gadget" / "items.json").read_text())
        old_sqlite = [f for f in items if f.get("fact") == "gadget uses SQLite"]
        assert old_sqlite[0].get("confidence") == "confirmed"  # Not yet superseded

        # Manual graduation should mark old fact as superseded
        cand_path = pipeline_life / "logs" / "candidates.jsonl"
        candidates = [json.loads(line) for line in cand_path.read_text().splitlines()]
        pg_cand = [c for c in candidates if "PostgreSQL" in c.get("fact", "")]
        assert pg_cand[0].get("needs_review") is True

        run_review(pipeline_life, "graduate", pg_cand[0]["id"], "verified DB migration")

        items = json.loads((pipeline_life / "Projects" / "gadget" / "items.json").read_text())
        old_sqlite = [f for f in items if f.get("fact") == "gadget uses SQLite"]
        assert old_sqlite[0].get("confidence") == "superseded"

        new_pg = [f for f in items if "PostgreSQL" in f.get("fact", "")]
        assert len(new_pg) == 1


class TestMockNightlyScript:
    """Test nightly-consolidate.sh with mock claude binary."""

    def test_nightly_with_mock_claude(self, pipeline_life: Path, tmp_path: Path):
        """Full nightly script with mock claude that returns valid JSON."""
        from datetime import date

        today_str = date.today().strftime("%Y-%m-%d")
        year = date.today().strftime("%Y")
        month = date.today().strftime("%m")

        mock_claude = tmp_path / "mock-claude"
        mock_output = json.dumps({
            "new_entities": [],
            "fact_updates": [
                {
                    "entity_type": "project",
                    "entity": "gadget",
                    "date": today_str,
                    "fact": "Deployed v3 via mock nightly run",
                    "category": "deployment",
                    "temporal": False,
                },
            ],
            "tacit_knowledge": [],
            "skills": [],
            "relationships": [],
            "summary": "Mock nightly run.",
        })
        mock_claude.write_text(f'#!/bin/bash\necho \'{mock_output}\'\n')
        mock_claude.chmod(0o755)

        # Create scripts symlink (topology check)
        scripts_link = pipeline_life / "scripts"
        scripts_link.symlink_to(CANONICAL)

        # Create daily note for today's actual date
        today_dir = pipeline_life / "Daily" / year / month
        today_dir.mkdir(parents=True, exist_ok=True)
        (today_dir / f"{today_str}.md").write_text(
            f"---\ndate: {today_str}\n---\n\n## Active Projects\n- gadget deployed v3\n\n## What We Worked On\n- Deployed gadget v3\n"
        )

        env = {
            **os.environ,
            "LIFE_DIR": str(pipeline_life),
            "CLAUDE_BIN": str(mock_claude),
            "HOME": str(tmp_path),
            "PATH": os.environ.get("PATH", ""),
            "LIFE_SYNC_ENABLED": "0",
            "TZ": "UTC",
        }

        result = subprocess.run(
            ["bash", str(CANONICAL / "nightly-consolidate.sh")],
            capture_output=True, text=True, env=env, timeout=60,
        )

        # Script may fail on optional steps (QMD, git) but extraction should work
        combined = result.stdout + result.stderr
        assert "Starting consolidation" in combined, f"Expected 'Starting consolidation' in:\n{combined}"
        assert "Applying extraction" in combined

        # Verify fact landed (either directly or via staging)
        items = json.loads((pipeline_life / "Projects" / "gadget" / "items.json").read_text())
        mock_facts = [f for f in items if "v3 via mock" in f.get("fact", "")]
        # In staging mode, facts go to candidates.jsonl first, then get auto-graduated
        if not mock_facts:
            cand_path = pipeline_life / "logs" / "candidates.jsonl"
            if cand_path.exists():
                candidates = [json.loads(line) for line in cand_path.read_text().splitlines()]
                staged = [c for c in candidates if "v3 via mock" in c.get("fact", "")]
                assert len(staged) >= 1 or len(mock_facts) >= 1, \
                    "Expected mock fact in items.json or candidates.jsonl"
            else:
                pytest.fail(f"No mock fact found. items: {[f['fact'] for f in items]}")

    def test_nightly_skips_empty_note(self, pipeline_life: Path, tmp_path: Path):
        """Nightly exits cleanly when no daily note exists for today."""
        scripts_link = pipeline_life / "scripts"
        scripts_link.symlink_to(CANONICAL)

        env = {
            **os.environ,
            "LIFE_DIR": str(pipeline_life),
            "CLAUDE_BIN": "/nonexistent/claude",
            "HOME": str(tmp_path),
            "PATH": os.environ.get("PATH", ""),
            "LIFE_SYNC_ENABLED": "0",
            "TZ": "UTC",
        }

        result = subprocess.run(
            ["bash", str(CANONICAL / "nightly-consolidate.sh")],
            capture_output=True, text=True, env=env, timeout=30,
        )

        combined = result.stdout + result.stderr
        # Today's date won't match the fixture dates, so it should skip
        assert "No daily note" in combined or result.returncode == 0
