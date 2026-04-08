"""Phase 3 tests — knowledge lint, index generation, session search.
All tests use real files in tmp_path. No mocking of services."""
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

LINT_SCRIPT = Path(__file__).parent.parent / "lint_knowledge.py"
INDEX_SCRIPT = Path(__file__).parent.parent / "generate_index.py"
SEARCH_SCRIPT = Path(__file__).parent.parent / "session_search.py"

TODAY = "2026-04-08"


@pytest.fixture
def life_dir(tmp_path: Path) -> Path:
    """Create a well-formed ~/life/ PARA structure."""
    # Projects
    proj = tmp_path / "Projects" / "test-proj"
    proj.mkdir(parents=True)
    (proj / "summary.md").write_text(
        "---\ntype: project\nname: Test Project\nstatus: active\n"
        "created: 2026-01-01\nlast-updated: 2026-04-07\n---\n"
        "A test project for validation.\n"
    )
    (proj / "items.json").write_text(json.dumps([
        {"date": "2026-04-01", "fact": "test fact", "category": "event",
         "confidence": "single", "mentions": 1, "last_seen": "2026-04-01"},
    ]))
    (tmp_path / "Projects" / "_template").mkdir()

    # People, Companies (empty but dirs exist)
    (tmp_path / "People" / "_template").mkdir(parents=True)
    (tmp_path / "Companies" / "_template").mkdir(parents=True)

    # Areas
    about = tmp_path / "Areas" / "about-me"
    about.mkdir(parents=True)
    (about / "profile.md").write_text("# Profile\nTest user\n")
    (about / "hard-rules.md").write_text("# Rules\nNo secrets\n")

    # Resources
    (tmp_path / "Resources" / "skills").mkdir(parents=True)

    # Daily notes
    daily = tmp_path / "Daily" / "2026" / "04"
    daily.mkdir(parents=True)
    (daily / "2026-04-07.md").write_text("---\ndate: 2026-04-07\n---\n## What We Worked On\n- testing\n")
    (daily / "2026-04-08.md").write_text("---\ndate: 2026-04-08\n---\n## Active Projects\n- [[test-proj]]\n")

    # Relationships
    (tmp_path / "relationships.json").write_text(json.dumps([
        {"from": "alice", "from_type": "person", "to": "test-proj", "to_type": "project",
         "relation": "works-on", "first_seen": "2026-03-01", "last_seen": "2026-04-01"},
    ]))

    # Archives
    (tmp_path / "Archives").mkdir()

    return tmp_path


def _run_script(script: Path, life_dir: Path, *extra_args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["LIFE_DIR"] = str(life_dir)
    env["CONSOLIDATION_DATE"] = TODAY
    return subprocess.run(
        [sys.executable, str(script), *extra_args],
        capture_output=True, text=True, env=env, timeout=30,
    )


# ── 3B: Knowledge Lint ────────────────────────────────────────────────────


class TestLintKnowledge:
    def test_detects_orphan_person(self, life_dir):
        """Person in relationships.json without People/{slug}/ dir → ORPHAN."""
        result = _run_script(LINT_SCRIPT, life_dir)
        assert "ORPHAN" in result.stdout
        assert "alice" in result.stdout

    def test_detects_orphan_company(self, life_dir):
        """Company in relationships.json without Companies/{slug}/ dir."""
        rels = [{"from": "acme", "from_type": "company", "to": "test-proj",
                 "to_type": "project", "relation": "provides",
                 "first_seen": "2026-01-01", "last_seen": "2026-04-01"}]
        (life_dir / "relationships.json").write_text(json.dumps(rels))
        result = _run_script(LINT_SCRIPT, life_dir)
        assert "acme" in result.stdout
        assert "company" in result.stdout.lower()

    def test_detects_orphan_project(self, life_dir):
        """Project in relationships.json without Projects/{slug}/ dir."""
        rels = [{"from": "alice", "from_type": "person", "to": "phantom",
                 "to_type": "project", "relation": "works-on",
                 "first_seen": "2026-01-01", "last_seen": "2026-04-01"}]
        (life_dir / "relationships.json").write_text(json.dumps(rels))
        result = _run_script(LINT_SCRIPT, life_dir)
        assert "phantom" in result.stdout

    def test_uses_type_map(self, life_dir):
        """Person checked in People/, not Projects/."""
        # alice is a person → checked in People/, not Projects/
        # Create People/alice to make it NOT an orphan
        (life_dir / "People" / "alice").mkdir()
        (life_dir / "People" / "alice" / "summary.md").write_text("# Alice\n")
        result = _run_script(LINT_SCRIPT, life_dir)
        assert "alice" not in result.stdout

    def test_detects_empty_entity_dir(self, life_dir):
        """Projects/empty/ with no summary.md → EMPTY_DIR."""
        (life_dir / "Projects" / "empty-proj").mkdir()
        result = _run_script(LINT_SCRIPT, life_dir)
        assert "EMPTY_DIR" in result.stdout
        assert "empty-proj" in result.stdout

    def test_detects_stale_relationship(self, life_dir):
        """Edge with last_seen 45 days ago → STALE."""
        old_date = str(date.fromisoformat(TODAY) - timedelta(days=45))
        rels = [{"from": "bob", "from_type": "person", "to": "test-proj",
                 "to_type": "project", "relation": "works-on",
                 "first_seen": old_date, "last_seen": old_date}]
        (life_dir / "relationships.json").write_text(json.dumps(rels))
        result = _run_script(LINT_SCRIPT, life_dir)
        assert "STALE" in result.stdout

    def test_no_stale_within_30_days(self, life_dir):
        """Edge with last_seen 20 days ago → not flagged as stale."""
        recent_date = str(date.fromisoformat(TODAY) - timedelta(days=20))
        rels = [{"from": "alice", "from_type": "person", "to": "test-proj",
                 "to_type": "project", "relation": "works-on",
                 "first_seen": recent_date, "last_seen": recent_date}]
        (life_dir / "relationships.json").write_text(json.dumps(rels))
        (life_dir / "People" / "alice").mkdir()
        (life_dir / "People" / "alice" / "summary.md").write_text("# Alice\n")
        result = _run_script(LINT_SCRIPT, life_dir)
        assert "STALE" not in result.stdout

    def test_clean_knowledge_base(self, life_dir):
        """Well-formed ~/life → zero findings."""
        # Create People/alice so no orphan
        (life_dir / "People" / "alice").mkdir()
        (life_dir / "People" / "alice" / "summary.md").write_text("# Alice\n")
        result = _run_script(LINT_SCRIPT, life_dir)
        assert "All clean" in result.stdout

    def test_json_output(self, life_dir):
        """--json produces valid JSON array."""
        result = _run_script(LINT_SCRIPT, life_dir, "--json")
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert all("category" in f and "message" in f for f in data)

    def test_missing_relationships_json(self, life_dir):
        """relationships.json doesn't exist → SKIP, no crash."""
        (life_dir / "relationships.json").unlink()
        result = _run_script(LINT_SCRIPT, life_dir)
        assert result.returncode == 0
        assert "SKIP" in result.stdout

    def test_malformed_relationships_json(self, life_dir):
        """Invalid JSON → SKIP, no crash."""
        (life_dir / "relationships.json").write_text("{invalid json")
        result = _run_script(LINT_SCRIPT, life_dir)
        assert result.returncode == 0
        assert "SKIP" in result.stdout

    def test_skips_template_dirs(self, life_dir):
        """_template/ dirs not flagged as empty."""
        result = _run_script(LINT_SCRIPT, life_dir)
        assert "_template" not in result.stdout

    def test_skips_dot_dirs(self, life_dir):
        """.git/ dirs not flagged."""
        (life_dir / "Projects" / ".hidden").mkdir()
        result = _run_script(LINT_SCRIPT, life_dir)
        assert ".hidden" not in result.stdout

    def test_entity_parent_dir_missing(self, life_dir):
        """People/ dir doesn't exist at all → no crash."""
        import shutil
        shutil.rmtree(life_dir / "People")
        result = _run_script(LINT_SCRIPT, life_dir)
        assert result.returncode == 0

    def test_deduplicates_slugs(self, life_dir):
        """Same slug in multiple edges → reported once."""
        rels = [
            {"from": "ghost", "from_type": "person", "to": "test-proj",
             "to_type": "project", "relation": "works-on",
             "first_seen": "2026-01-01", "last_seen": "2026-04-01"},
            {"from": "ghost", "from_type": "person", "to": "test-proj",
             "to_type": "project", "relation": "manages",
             "first_seen": "2026-02-01", "last_seen": "2026-04-01"},
        ]
        (life_dir / "relationships.json").write_text(json.dumps(rels))
        result = _run_script(LINT_SCRIPT, life_dir, "--json")
        data = json.loads(result.stdout)
        ghost_findings = [f for f in data if "ghost" in f["message"]]
        assert len(ghost_findings) == 1


# ── 3C: Index Generation ─────────────────────────────────────────────────


class TestGenerateIndex:
    def test_generates_index_with_projects(self, life_dir):
        """Projects listed with wiki-links in output."""
        result = _run_script(INDEX_SCRIPT, life_dir)
        assert result.returncode == 0
        index = (life_dir / "index.md").read_text()
        assert "[[test-proj]]" in index
        assert "1 facts" in index

    def test_empty_sections_show_placeholder(self, life_dir):
        """No People entities → '_(none yet)_'."""
        _run_script(INDEX_SCRIPT, life_dir)
        index = (life_dir / "index.md").read_text()
        assert "_(none yet)_" in index

    def test_includes_stats(self, life_dir):
        """Stats section includes markdown file count."""
        _run_script(INDEX_SCRIPT, life_dir)
        index = (life_dir / "index.md").read_text()
        assert "markdown files" in index

    def test_dry_run_no_write(self, life_dir):
        """--dry-run prints but doesn't create index.md."""
        result = _run_script(INDEX_SCRIPT, life_dir, "--dry-run")
        assert result.returncode == 0
        assert "[[test-proj]]" in result.stdout
        assert not (life_dir / "index.md").exists()

    def test_skip_write_if_unchanged(self, life_dir):
        """Second run with same data skips write."""
        _run_script(INDEX_SCRIPT, life_dir)
        result = _run_script(INDEX_SCRIPT, life_dir)
        assert "No changes" in result.stdout

    def test_summary_only_frontmatter(self, life_dir):
        """summary.md with only frontmatter → uses dir name."""
        proj2 = life_dir / "Projects" / "bare-proj"
        proj2.mkdir()
        (proj2 / "summary.md").write_text("---\ntype: project\nstatus: active\n---\n")
        _run_script(INDEX_SCRIPT, life_dir)
        index = (life_dir / "index.md").read_text()
        assert "[[bare-proj]]" in index

    def test_missing_items_json(self, life_dir):
        """Project without items.json → shows 0 facts (no crash)."""
        (life_dir / "Projects" / "test-proj" / "items.json").unlink()
        result = _run_script(INDEX_SCRIPT, life_dir)
        assert result.returncode == 0
        index = (life_dir / "index.md").read_text()
        assert "[[test-proj]]" in index
        # Should not show "facts" when count is 0
        assert "0 facts" not in index


# ── 3D: Session Search ────────────────────────────────────────────────────


@pytest.fixture
def search_dir(tmp_path: Path) -> Path:
    """Create a tmp dir with sample session digest JSONL files."""
    daily = tmp_path / "Daily" / "2026" / "04"
    daily.mkdir(parents=True)

    digests = [
        {"session_id": "aaa-111", "ts": "2026-04-01T10:00:00Z", "summary": "Fixed auth bug in gateway",
         "session_type": "coding", "decisions": ["switched to JWT"], "files_touched": ["/src/auth.py"],
         "tool_counts": {"Edit": 5}, "msg_count": 20},
        {"session_id": "bbb-222", "ts": "2026-04-02T14:00:00Z", "summary": "Researched caching strategies",
         "session_type": "research", "decisions": [], "files_touched": [],
         "tool_counts": {"Read": 10}, "msg_count": 15},
        {"session_id": "ccc-333", "ts": "2026-04-03T09:00:00Z", "summary": "Deployed polymarket bot update",
         "session_type": "ops", "decisions": ["use redis for rate limiting"],
         "files_touched": ["/deploy.sh"], "tool_counts": {"Bash": 8}, "msg_count": 30},
    ]

    lines = "\n".join(json.dumps(d) for d in digests) + "\n"
    (daily / "sessions-digest-2026-04-01.jsonl").write_text(lines)

    # Empty file (edge case)
    (daily / "sessions-digest-2026-04-04.jsonl").write_text("")

    # Entry missing session_type (schema evolution)
    old_entry = {"session_id": "ddd-444", "ts": "2026-04-01T08:00:00Z",
                 "summary": "hello", "tool_counts": {"Read": 1}, "msg_count": 5}
    (daily / "sessions-digest-2026-04-05.jsonl").write_text(json.dumps(old_entry) + "\n")

    return tmp_path


class TestSessionSearch:
    def test_backfill_creates_db(self, search_dir):
        """Backfill ingests JSONL files and creates DB."""
        result = _run_script(SEARCH_SCRIPT, search_dir, "--backfill")
        assert result.returncode == 0
        assert "Backfilled 4 sessions" in result.stdout
        assert (search_dir / "sessions.db").exists()

    def test_search_finds_decision(self, search_dir):
        """Search for 'JWT' finds the auth session."""
        _run_script(SEARCH_SCRIPT, search_dir, "--backfill")
        result = _run_script(SEARCH_SCRIPT, search_dir, "--query", "JWT")
        assert "auth" in result.stdout.lower() or "gateway" in result.stdout.lower()

    def test_search_filters_by_type(self, search_dir):
        """--type coding only returns coding sessions."""
        _run_script(SEARCH_SCRIPT, search_dir, "--backfill")
        result = _run_script(SEARCH_SCRIPT, search_dir, "--type", "coding", "--recent", "10", "--json")
        data = json.loads(result.stdout)
        assert all(d["session_type"] == "coding" for d in data)

    def test_duplicate_insert_idempotent(self, search_dir):
        """Backfill twice → same count, no duplicates."""
        _run_script(SEARCH_SCRIPT, search_dir, "--backfill")
        result = _run_script(SEARCH_SCRIPT, search_dir, "--backfill")
        assert "Backfilled 0 sessions" in result.stdout

    def test_empty_query_returns_recent(self, search_dir):
        """No query, --recent 2 → 2 most recent sessions."""
        _run_script(SEARCH_SCRIPT, search_dir, "--backfill")
        result = _run_script(SEARCH_SCRIPT, search_dir, "--recent", "2", "--json")
        data = json.loads(result.stdout)
        assert len(data) == 2
        # Most recent first
        assert data[0]["ts"] > data[1]["ts"]

    def test_index_stdin_mode(self, search_dir):
        """Pipe JSON to --index-stdin → indexed in DB."""
        # First ensure DB exists
        _run_script(SEARCH_SCRIPT, search_dir, "--backfill")
        # Index a new session via stdin
        new_digest = {"session_id": "eee-555", "ts": "2026-04-08T12:00:00Z",
                      "summary": "Implemented unicorn feature",
                      "session_type": "coding", "decisions": [], "files_touched": [],
                      "tool_counts": {}, "msg_count": 10}
        env = os.environ.copy()
        env["LIFE_DIR"] = str(search_dir)
        result = subprocess.run(
            [sys.executable, str(SEARCH_SCRIPT), "--index-stdin"],
            input=json.dumps(new_digest), text=True, capture_output=True,
            env=env, timeout=10,
        )
        assert result.returncode == 0
        # Verify it's searchable
        result = _run_script(SEARCH_SCRIPT, search_dir, "--query", "unicorn")
        assert "unicorn" in result.stdout.lower()

    def test_fts5_special_chars(self, search_dir):
        """Query with special FTS5 chars → no crash."""
        _run_script(SEARCH_SCRIPT, search_dir, "--backfill")
        result = _run_script(SEARCH_SCRIPT, search_dir, "--query", '"unmatched quote')
        # Should not crash, may show error or empty results
        assert result.returncode == 0

    def test_rebuild_deletes_and_recreates(self, search_dir):
        """--rebuild deletes DB and backfills fresh."""
        _run_script(SEARCH_SCRIPT, search_dir, "--backfill")
        result = _run_script(SEARCH_SCRIPT, search_dir, "--rebuild")
        assert "DB deleted" in result.stdout
        assert "Backfilled 4 sessions" in result.stdout

    def test_missing_digest_fields(self, search_dir):
        """Digest without decisions/files_touched → defaults used."""
        _run_script(SEARCH_SCRIPT, search_dir, "--backfill")
        # The old_entry (ddd-444) lacks session_type, decisions, files_touched
        result = _run_script(SEARCH_SCRIPT, search_dir, "--recent", "10", "--json")
        data = json.loads(result.stdout)
        old = [d for d in data if d["session_id"] == "ddd-444"]
        assert len(old) == 1
        assert old[0]["session_type"] == "unknown"
