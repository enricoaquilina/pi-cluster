"""Phase 3b tests — session hook, wiki-link lint, maxwell indexing, raw ingest.
All tests use real files in tmp_path. No mocking."""
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

HOOK_SCRIPT = Path(__file__).parent.parent / "cc_start_hook.sh"
LINT_SCRIPT = Path(__file__).parent.parent / "lint_knowledge.py"
SEARCH_SCRIPT = Path(__file__).parent.parent / "session_search.py"
INGEST_SCRIPT = Path(__file__).parent.parent / "ingest_raw.py"

TODAY = "2026-04-08"

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def life_dir(tmp_path: Path) -> Path:
    """Create ~/life/ structure for testing."""
    proj = tmp_path / "Projects" / "test-proj"
    proj.mkdir(parents=True)
    (proj / "summary.md").write_text(
        "---\ntype: project\nstatus: active\nlast-updated: 2026-04-07\n---\nTest project.\n"
    )
    (proj / "items.json").write_text("[]")
    (tmp_path / "Projects" / "_template").mkdir()
    (tmp_path / "Projects" / "openclaw-maxwell").mkdir()  # empty dir

    (tmp_path / "People" / "_template").mkdir(parents=True)
    (tmp_path / "Companies" / "_template").mkdir(parents=True)
    (tmp_path / "Areas" / "about-me").mkdir(parents=True)
    (tmp_path / "Resources" / "skills").mkdir(parents=True)
    (tmp_path / "Archives").mkdir()

    daily = tmp_path / "Daily" / "2026" / "04"
    daily.mkdir(parents=True)
    (daily / "2026-04-07.md").write_text(
        "## Active Projects\n- [[test-proj]] — testing\n- [[openclaw]] — coding\n"
    )
    (daily / "2026-04-08.md").write_text(
        "## Active Projects\n- [[test-proj]]\n"
    )

    (tmp_path / "relationships.json").write_text(json.dumps([
        {"from": "alice", "from_type": "person", "to": "test-proj",
         "to_type": "project", "relation": "works-on",
         "first_seen": "2026-03-01", "last_seen": "2026-04-01"},
    ]))

    (tmp_path / "logs").mkdir()
    (tmp_path / "raw").mkdir()
    return tmp_path


def _run(script: Path, life_dir: Path, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["LIFE_DIR"] = str(life_dir)
    env["CONSOLIDATION_DATE"] = TODAY
    env["LIFE_GIT_SYNC_DISABLED"] = "1"
    return subprocess.run(
        [sys.executable, str(script), *args],
        input=stdin, capture_output=True, text=True, env=env, timeout=30,
    )


def _run_hook(life_dir: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["LIFE_DIR"] = str(life_dir)
    env["LIFE_GIT_SYNC_DISABLED"] = "1"
    return subprocess.run(
        ["bash", str(HOOK_SCRIPT)],
        capture_output=True, text=True, env=env, timeout=15,
    )


# ── 3b-1: Session Search in Start Hook ───────────────────────────────────


class TestRecentSessionsHook:
    def test_recent_sessions_shown(self, life_dir):
        """Start hook shows Recent Sessions when DB has entries."""
        from session_search import ensure_db, index_session
        db = life_dir / "sessions.db"
        conn = ensure_db(db)
        index_session(conn, {
            "session_id": "test-1", "ts": "2026-04-08T10:00:00Z",
            "summary": "Fixed the auth bug in gateway", "session_type": "coding",
            "decisions": [], "files_touched": [], "tool_counts": {}, "msg_count": 10,
        })
        conn.close()
        # Hook looks for $LIFE_DIR/scripts/session_search.py
        scripts_dir = life_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        import shutil
        shutil.copy2(SEARCH_SCRIPT, scripts_dir / "session_search.py")
        result = _run_hook(life_dir)
        assert result.returncode == 0
        assert "Recent Sessions" in result.stdout
        assert "auth bug" in result.stdout.lower() or "coding" in result.stdout

    def test_no_sessions_db(self, life_dir):
        """No sessions.db → hook still exits 0."""
        result = _run_hook(life_dir)
        assert result.returncode == 0


# ── 3b-2: Wiki-link Lint ─────────────────────────────────────────────────


class TestWikiLinkLint:
    def test_wikilink_resolves_via_alias(self, life_dir):
        """[[openclaw]] with alias → openclaw-maxwell → no BROKEN_LINK."""
        (life_dir / ".wiki-link-aliases").write_text("openclaw = openclaw-maxwell\n")
        result = _run(LINT_SCRIPT, life_dir)
        assert "BROKEN_LINK" not in result.stdout
        # openclaw-maxwell dir exists (empty), so alias resolves

    def test_wikilink_skip_alias(self, life_dir):
        """[[mission-control]] with SKIP → no BROKEN_LINK."""
        daily = life_dir / "Daily" / "2026" / "04" / "2026-04-08.md"
        daily.write_text("Testing [[mission-control]] stuff\n")
        (life_dir / ".wiki-link-aliases").write_text("mission-control = SKIP\n")
        result = _run(LINT_SCRIPT, life_dir)
        assert "mission-control" not in result.stdout or "BROKEN_LINK" not in result.stdout

    def test_wikilink_flags_unknown(self, life_dir):
        """[[nonexistent]] without alias → BROKEN_LINK."""
        daily = life_dir / "Daily" / "2026" / "04" / "2026-04-08.md"
        daily.write_text("Check [[nonexistent]] for details\n")
        result = _run(LINT_SCRIPT, life_dir)
        assert "BROKEN_LINK" in result.stdout
        assert "nonexistent" in result.stdout

    def test_wikilink_no_aliases_file(self, life_dir):
        """No .wiki-link-aliases → all unresolved flagged."""
        daily = life_dir / "Daily" / "2026" / "04" / "2026-04-08.md"
        daily.write_text("Check [[openclaw]] thing\n")
        # openclaw-maxwell dir exists but no alias to resolve [[openclaw]]
        result = _run(LINT_SCRIPT, life_dir)
        assert "BROKEN_LINK" in result.stdout
        assert "openclaw" in result.stdout

    def test_wikilink_old_note_skipped(self, life_dir):
        """Note from 60 days ago → not scanned for wiki-links."""
        old_date = str(date.fromisoformat(TODAY) - timedelta(days=60))
        parts = old_date.split("-")
        old_dir = life_dir / "Daily" / parts[0] / parts[1]
        old_dir.mkdir(parents=True, exist_ok=True)
        (old_dir / f"{old_date}.md").write_text("Old [[nonexistent]] note\n")
        # Remove recent notes that might flag
        for f in (life_dir / "Daily" / "2026" / "04").glob("2026-04-*.md"):
            f.unlink()
        result = _run(LINT_SCRIPT, life_dir)
        assert "BROKEN_LINK" not in result.stdout

    def test_wikilink_deduplicates(self, life_dir):
        """Same slug in 2 notes → reported once."""
        d = life_dir / "Daily" / "2026" / "04"
        (d / "2026-04-07.md").write_text("[[ghost-entity]] here\n")
        (d / "2026-04-08.md").write_text("[[ghost-entity]] there\n")
        result = _run(LINT_SCRIPT, life_dir, "--json")
        data = json.loads(result.stdout)
        ghost = [f for f in data if "ghost-entity" in f.get("message", "")]
        assert len(ghost) == 1


# ── 3b-3: Maxwell FTS5 Indexing ──────────────────────────────────────────


@pytest.fixture
def maxwell_dir(tmp_path: Path) -> Path:
    """Create tmp dir with maxwell markdown files."""
    daily = tmp_path / "Daily" / "2026" / "04"
    daily.mkdir(parents=True)

    (daily / "maxwell-2026-04-07.md").write_text(
        "---\ndate: 2026-04-07\nsource: maxwell-ingest\n---\n\n"
        "## Maxwell Dispatch Activity\n"
        "- 15:17 \u2014 **Archie** (coder): fix the bug (14s, success)\n\n"
        "## Heartbeat Actions\n"
        "- 00:00 \u2014 Gym Tracker App: awaiting_approval \u2192 Pixel\n"
        "- 00:30 \u2014 Gym Tracker App: awaiting_approval \u2192 Pixel\n"
        "- 01:00 \u2014 Gym Tracker App: awaiting_approval \u2192 Pixel\n"
    )

    (daily / "maxwell-2026-04-08.md").write_text(
        "---\ndate: 2026-04-08\nsource: maxwell-ingest\n---\n\n"
        "## Maxwell Dispatch Activity\n"
        "_No dispatch activity today._\n\n"
        "## Heartbeat Actions\n"
        "- 00:00 \u2014 Gym Tracker App: awaiting_approval \u2192 Pixel\n"
        "- 00:30 \u2014 Gym Tracker App: awaiting_approval \u2192 Pixel\n"
    )

    (tmp_path / "logs").mkdir()
    return tmp_path


class TestMaxwellIndexing:
    def test_backfill_maxwell(self, maxwell_dir):
        """2 maxwell files → 2 records with session_type=maxwell."""
        result = _run(SEARCH_SCRIPT, maxwell_dir, "--backfill-maxwell")
        assert "Backfilled 2 maxwell" in result.stdout
        # Verify searchable
        result = _run(SEARCH_SCRIPT, maxwell_dir, "--type", "maxwell", "--recent", "10", "--json")
        data = json.loads(result.stdout)
        assert len(data) == 2
        assert all(d["session_type"] == "maxwell" for d in data)

    def test_maxwell_deduplicates_heartbeat(self, maxwell_dir):
        """Heartbeat summary says '(3x)' not 3 separate lines."""
        _run(SEARCH_SCRIPT, maxwell_dir, "--backfill-maxwell")
        result = _run(SEARCH_SCRIPT, maxwell_dir, "--query", "gym tracker", "--json")
        data = json.loads(result.stdout)
        assert any("3x" in d.get("summary", "") or "2x" in d.get("summary", "") for d in data)

    def test_maxwell_upsert_updates_content(self, maxwell_dir):
        """Re-index with changed content → summary updated."""
        _run(SEARCH_SCRIPT, maxwell_dir, "--backfill-maxwell")
        # Add more heartbeat lines to the file
        daily = maxwell_dir / "Daily" / "2026" / "04"
        (daily / "maxwell-2026-04-08.md").write_text(
            "---\ndate: 2026-04-08\n---\n\n"
            "## Heartbeat Actions\n"
            + "".join(f"- {h:02d}:00 \u2014 New App: deployed \u2192 Bot\n" for h in range(10))
        )
        result = _run(SEARCH_SCRIPT, maxwell_dir, "--backfill-maxwell")
        assert "Backfilled 2 maxwell" in result.stdout  # force=True re-inserts
        result = _run(SEARCH_SCRIPT, maxwell_dir, "--query", "New App", "--json")
        data = json.loads(result.stdout)
        assert len(data) > 0
        assert "10x" in data[0]["summary"]

    def test_search_finds_maxwell(self, maxwell_dir):
        """--query 'Archie' finds the dispatch."""
        _run(SEARCH_SCRIPT, maxwell_dir, "--backfill-maxwell")
        result = _run(SEARCH_SCRIPT, maxwell_dir, "--query", "Archie")
        assert "Archie" in result.stdout

    def test_type_filter_excludes_maxwell(self, maxwell_dir):
        """--type coding doesn't return maxwell entries."""
        _run(SEARCH_SCRIPT, maxwell_dir, "--backfill-maxwell")
        result = _run(SEARCH_SCRIPT, maxwell_dir, "--type", "coding", "--recent", "10", "--json")
        data = json.loads(result.stdout)
        assert all(d["session_type"] != "maxwell" for d in data)

    def test_maxwell_no_content(self, tmp_path):
        """File with only frontmatter → skipped."""
        daily = tmp_path / "Daily" / "2026" / "04"
        daily.mkdir(parents=True)
        (daily / "maxwell-2026-04-01.md").write_text("---\ndate: 2026-04-01\n---\n")
        (tmp_path / "logs").mkdir()
        result = _run(SEARCH_SCRIPT, tmp_path, "--backfill-maxwell")
        assert "Backfilled 0 maxwell" in result.stdout

    def test_force_false_skips_existing(self, maxwell_dir):
        """Regular session with same ID → not overwritten when force=False."""
        from session_search import ensure_db, index_session
        db = maxwell_dir / "sessions.db"
        conn = ensure_db(db)
        index_session(conn, {
            "session_id": "test-1", "ts": "2026-04-08T10:00:00Z",
            "summary": "Original summary", "session_type": "coding",
            "decisions": [], "files_touched": [], "tool_counts": {}, "msg_count": 10,
        })
        # Try to overwrite without force
        result = index_session(conn, {
            "session_id": "test-1", "ts": "2026-04-08T10:00:00Z",
            "summary": "Updated summary", "session_type": "coding",
            "decisions": [], "files_touched": [], "tool_counts": {}, "msg_count": 10,
        }, force=False)
        assert result is False
        # Verify original preserved
        row = conn.execute("SELECT summary FROM sessions WHERE session_id='test-1'").fetchone()
        assert "Original" in row[0]
        conn.close()


# ── 3b-4: Karpathy Ingest ────────────────────────────────────────────────


class TestIngestRaw:
    def test_dry_run_prints_prompt(self, life_dir):
        """--dry-run prints prompt with source text."""
        raw = life_dir / "raw" / "test.md"
        raw.write_text("# Redis\nRedis is great for caching.\n")
        result = _run(INGEST_SCRIPT, life_dir, str(raw), "--dry-run")
        assert result.returncode == 0
        assert "Redis" in result.stdout
        assert "EXISTING ENTITIES" in result.stdout

    def test_dry_run_includes_entity_list(self, life_dir):
        """Prompt includes existing entity slugs."""
        raw = life_dir / "raw" / "test.md"
        raw.write_text("Some content.\n")
        result = _run(INGEST_SCRIPT, life_dir, str(raw), "--dry-run")
        assert "test-proj" in result.stdout

    def test_validates_text_file(self, life_dir):
        """Binary file rejected."""
        raw = life_dir / "raw" / "binary.md"
        raw.write_bytes(b"\x00\x01\x02\x03binary content")
        result = _run(INGEST_SCRIPT, life_dir, str(raw), "--dry-run")
        assert "binary" in result.stdout.lower() or "SKIP" in result.stdout

    def test_truncates_large_file(self, life_dir):
        """File >50K chars truncated."""
        raw = life_dir / "raw" / "huge.md"
        raw.write_text("x" * 60_000)
        result = _run(INGEST_SCRIPT, life_dir, str(raw), "--dry-run")
        assert "truncated" in result.stdout.lower()

    def test_all_mode_dry_run(self, life_dir):
        """--all --dry-run processes multiple files."""
        (life_dir / "raw" / "a.md").write_text("Article A\n")
        (life_dir / "raw" / "b.md").write_text("Article B\n")
        result = _run(INGEST_SCRIPT, life_dir, "--all", "--dry-run")
        assert "a.md" in result.stdout
        assert "b.md" in result.stdout

    def test_skips_processed_dir(self, life_dir):
        """Files in raw/_processed/ ignored by --all."""
        processed = life_dir / "raw" / "_processed"
        processed.mkdir()
        (processed / "old.md").write_text("Already processed\n")
        # No files in raw/ itself
        result = _run(INGEST_SCRIPT, life_dir, "--all", "--dry-run")
        assert "No files to process" in result.stdout

    def test_claude_cli_missing(self, life_dir):
        """Non-existent claude binary → exit 1 with error."""
        raw = life_dir / "raw" / "test.md"
        raw.write_text("Content\n")
        env = os.environ.copy()
        env["LIFE_DIR"] = str(life_dir)
        env["CONSOLIDATION_DATE"] = TODAY
        env["PATH"] = "/nonexistent"  # ensure claude not found
        result = subprocess.run(
            [sys.executable, str(INGEST_SCRIPT), str(raw)],
            capture_output=True, text=True, env=env, timeout=15,
        )
        assert result.returncode != 0 or "ERROR" in result.stdout or "ERROR" in result.stderr
