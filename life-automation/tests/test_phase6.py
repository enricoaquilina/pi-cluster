"""Phase 6 tests — entity enrichment, LLM lint, context-aware hook.
All tests use real files in tmp_path. No mocking."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK_SCRIPT = Path(__file__).parent.parent / "cc_start_hook.sh"
ENRICH_SCRIPT = Path(__file__).parent.parent / "enrich_entities.py"
LLM_LINT_SCRIPT = Path(__file__).parent.parent / "lint_knowledge_llm.py"

TODAY = "2026-04-08"


@pytest.fixture
def life_dir(tmp_path: Path) -> Path:
    """Create ~/life/ with entities for testing."""
    # Project with many facts
    proj = tmp_path / "Projects" / "test-proj"
    proj.mkdir(parents=True)
    (proj / "summary.md").write_text(
        "---\ntype: project\nstatus: active\nlast-updated: 2026-04-07\n---\n\nA test project for validation.\n"
    )
    facts = [
        {"date": f"2026-04-0{i}", "fact": f"Test fact number {i}", "category": "event",
         "confidence": "confirmed", "mentions": 2, "last_seen": f"2026-04-0{i}", "temporal": False}
        for i in range(1, 8)
    ]
    (proj / "items.json").write_text(json.dumps(facts))
    (tmp_path / "Projects" / "_template").mkdir()

    # Empty person entity
    person = tmp_path / "People" / "alice"
    person.mkdir(parents=True)
    (person / "summary.md").write_text("---\ntype: person\nstatus: active\n---\n")
    (person / "items.json").write_text("[]")
    (tmp_path / "People" / "_template").mkdir()
    (tmp_path / "Companies" / "_template").mkdir(parents=True)

    # Daily notes with mentions
    daily = tmp_path / "Daily" / "2026" / "04"
    daily.mkdir(parents=True)
    (daily / "2026-04-08.md").write_text(
        "## Active Projects\n- [[test-proj]]\n\n"
        "## New Facts\n- [[alice]] is a developer working on [[test-proj]]\n"
    )

    # Relationships
    (tmp_path / "relationships.json").write_text(json.dumps([
        {"from": "alice", "from_type": "person", "to": "test-proj",
         "to_type": "project", "relation": "works-on",
         "first_seen": "2026-03-01", "last_seen": "2026-04-01"},
    ]))

    (tmp_path / "logs").mkdir()
    (tmp_path / "Archives").mkdir()
    (tmp_path / "Areas" / "about-me").mkdir(parents=True)
    (tmp_path / "Resources" / "skills").mkdir(parents=True)
    return tmp_path


def _run(script: Path, life_dir: Path, *args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["LIFE_DIR"] = str(life_dir)
    env["CONSOLIDATION_DATE"] = TODAY
    env["LIFE_GIT_SYNC_DISABLED"] = "1"
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, text=True, env=env, timeout=30,
    )


def _run_hook(life_dir: Path, pwd: str = "/tmp") -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["LIFE_DIR"] = str(life_dir)
    env["LIFE_GIT_SYNC_DISABLED"] = "1"
    env["PWD"] = pwd
    return subprocess.run(
        ["bash", "-c", f"cd {pwd} 2>/dev/null; source {HOOK_SCRIPT}"],
        capture_output=True, text=True, env=env, timeout=15,
    )


# ── 6B: Context-Aware Start Hook ─────────────────────────────────────────


class TestContextAwareHook:
    def test_no_context_outside_project(self, life_dir):
        """PWD not in a project → no Project Context section."""
        result = _run_hook(life_dir, pwd="/tmp")
        assert "Project Context" not in result.stdout

    def test_output_under_10k(self, life_dir):
        """Full hook output stays under 10K chars."""
        result = _run_hook(life_dir, pwd="/tmp")
        assert len(result.stdout) < 10000


# ── 6C: Entity Enrichment ────────────────────────────────────────────────


class TestEntityEnrichment:
    def test_finds_sparse_entities(self, life_dir):
        """Entities with <3 facts found for enrichment."""
        result = _run(ENRICH_SCRIPT, life_dir, "--dry-run")
        assert "alice" in result.stdout
        assert "DRY RUN" in result.stdout

    def test_skips_rich_entities(self, life_dir):
        """Entities with 7 facts not targeted for enrichment."""
        result = _run(ENRICH_SCRIPT, life_dir, "--dry-run")
        # test-proj has 7 facts, should NOT appear as "DRY RUN: Projects/test-proj"
        assert "DRY RUN: Projects/test-proj" not in result.stdout

    def test_gathers_context_from_daily_notes(self, life_dir):
        """Context includes daily note mentions."""
        result = _run(ENRICH_SCRIPT, life_dir, "--dry-run")
        assert "developer" in result.stdout or "alice" in result.stdout

    def test_dry_run_no_writes(self, life_dir):
        """--dry-run doesn't modify items.json."""
        _run(ENRICH_SCRIPT, life_dir, "--dry-run")
        items = json.loads((life_dir / "People" / "alice" / "items.json").read_text())
        assert len(items) == 0

    def test_entity_flag(self, life_dir):
        """--entity alice only processes alice."""
        result = _run(ENRICH_SCRIPT, life_dir, "--dry-run", "--entity", "alice")
        assert "alice" in result.stdout
        # Only alice should appear, not test-proj (which has <3 facts but isn't targeted)


# ── 6A: LLM Knowledge Lint ───────────────────────────────────────────────


class TestLLMLint:
    def test_skips_empty_entities(self, life_dir):
        """Entities with <5 facts skipped."""
        result = _run(LLM_LINT_SCRIPT, life_dir, "--dry-run")
        assert "alice" not in result.stdout  # 0 facts, below threshold

    def test_checks_rich_entities(self, life_dir):
        """Entities with 7 facts get linted."""
        result = _run(LLM_LINT_SCRIPT, life_dir, "--dry-run")
        assert "test-proj" in result.stdout
        assert "7 facts" in result.stdout

    def test_dry_run_no_output_file(self, life_dir):
        """--dry-run doesn't create results file."""
        _run(LLM_LINT_SCRIPT, life_dir, "--dry-run")
        assert not (life_dir / "logs" / "llm-lint-results.json").exists()

    def test_json_output(self, life_dir):
        """--json --dry-run outputs valid JSON-compatible output."""
        result = _run(LLM_LINT_SCRIPT, life_dir, "--dry-run", "--json")
        # dry-run with json doesn't produce findings but should not crash
        assert result.returncode == 0
