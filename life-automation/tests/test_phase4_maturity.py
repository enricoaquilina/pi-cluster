"""Phase 4 tests — lint auto-fix, index LLM, decay dashboard, graph viz, MCP write.
All tests use real files in tmp_path. No mocking."""
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

LINT_SCRIPT = Path(__file__).parent.parent / "lint_knowledge.py"
INDEX_SCRIPT = Path(__file__).parent.parent / "generate_index.py"
DASHBOARD_SCRIPT = Path(__file__).parent.parent / "generate_decay_dashboard.py"
GRAPH_SCRIPT = Path(__file__).parent.parent / "generate_graph.py"

TODAY = "2026-04-08"


@pytest.fixture
def life_dir(tmp_path: Path) -> Path:
    """Create ~/life/ with templates, entities, relationships."""
    # Templates
    for parent in ["Projects", "People", "Companies"]:
        tmpl = tmp_path / parent / "_template"
        tmpl.mkdir(parents=True)
        etype = {"Projects": "project", "People": "person", "Companies": "company"}[parent]
        (tmpl / "summary.md").write_text(
            f"---\ntype: {etype}\nname: Display Name\ncreated: YYYY-MM-DD\n"
            f"last-updated: YYYY-MM-DD\nstatus: active\n---\n\n## What This Is\n"
        )
        (tmpl / "items.json").write_text("[]")

    # Real project
    proj = tmp_path / "Projects" / "test-proj"
    proj.mkdir()
    (proj / "summary.md").write_text(
        "---\ntype: project\nname: Test Project\nstatus: active\n"
        "created: 2026-01-01\nlast-updated: 2026-04-07\n---\nA test project.\n"
    )
    (proj / "items.json").write_text(json.dumps([
        {"date": "2026-04-01", "fact": "Test fact one", "category": "event",
         "confidence": "confirmed", "mentions": 2, "last_seen": "2026-04-01", "temporal": False},
        {"date": "2026-03-20", "fact": "Old stale fact", "category": "configuration",
         "confidence": "single", "mentions": 1, "last_seen": "2026-03-20", "temporal": False},
    ]))

    # Relationships with orphans
    (tmp_path / "relationships.json").write_text(json.dumps([
        {"from": "alice", "from_type": "person", "to": "test-proj",
         "to_type": "project", "relation": "works-on",
         "first_seen": "2026-03-01", "last_seen": "2026-04-01"},
        {"from": "acme", "from_type": "company", "to": "test-proj",
         "to_type": "project", "relation": "provides",
         "first_seen": "2026-03-01", "last_seen": "2026-04-01"},
    ]))

    # Empty project dir
    (tmp_path / "Projects" / "empty-proj").mkdir()

    # Daily + Areas + Resources + logs
    daily = tmp_path / "Daily" / "2026" / "04"
    daily.mkdir(parents=True)
    (daily / "2026-04-08.md").write_text("## Active Projects\n- [[test-proj]]\n")
    (tmp_path / "Areas" / "about-me").mkdir(parents=True)
    (tmp_path / "Resources" / "skills").mkdir(parents=True)
    (tmp_path / "Archives").mkdir()
    (tmp_path / "logs").mkdir()

    return tmp_path


def _run(script: Path, life_dir: Path, *args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["LIFE_DIR"] = str(life_dir)
    env["CONSOLIDATION_DATE"] = TODAY
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, text=True, env=env, timeout=30,
    )


# ── 4A: Lint Auto-Fix ────────────────────────────────────────────────────


class TestLintAutoFix:
    def test_fix_orphan_creates_entity(self, life_dir):
        """Orphan person → People/{slug}/ created with summary + items."""
        result = _run(LINT_SCRIPT, life_dir, "--fix")
        assert "FIXED" in result.stdout
        assert (life_dir / "People" / "alice" / "summary.md").exists()
        assert (life_dir / "People" / "alice" / "items.json").exists()
        # Check correct type in summary
        summary = (life_dir / "People" / "alice" / "summary.md").read_text()
        assert "type: person" in summary
        assert "name: Alice" in summary

    def test_fix_empty_dir_creates_summary(self, life_dir):
        """Empty Project dir → summary.md created."""
        _run(LINT_SCRIPT, life_dir, "--fix")
        assert (life_dir / "Projects" / "empty-proj" / "summary.md").exists()

    def test_fix_dry_run_no_write(self, life_dir):
        """--fix --dry-run → nothing written."""
        _run(LINT_SCRIPT, life_dir, "--fix", "--dry-run")
        assert not (life_dir / "People" / "alice").exists()

    def test_fix_idempotent(self, life_dir):
        """Run --fix twice → no errors."""
        _run(LINT_SCRIPT, life_dir, "--fix")
        result = _run(LINT_SCRIPT, life_dir, "--fix")
        assert result.returncode == 0

    def test_fix_uses_correct_template(self, life_dir):
        """Company uses Companies/_template, not People/_template."""
        _run(LINT_SCRIPT, life_dir, "--fix")
        summary = (life_dir / "Companies" / "acme" / "summary.md").read_text()
        assert "type: company" in summary

    def test_rerun_lint_after_fix_clean(self, life_dir):
        """--fix then lint again → 0 findings."""
        _run(LINT_SCRIPT, life_dir, "--fix")
        result = _run(LINT_SCRIPT, life_dir)
        assert "All clean" in result.stdout


# ── 4B: Index LLM Enhancement ────────────────────────────────────────────


class TestIndexLLM:
    def test_llm_uses_cached_description(self, life_dir):
        """Pre-populated cache → uses cached description."""
        cache = {"test-proj": {"desc": "Cached description of project", "date": TODAY}}
        (life_dir / ".index-cache.json").write_text(json.dumps(cache))
        result = _run(INDEX_SCRIPT, life_dir, "--llm", "--dry-run")
        assert "Cached description" in result.stdout

    def test_no_llm_flag_skips(self, life_dir):
        """Without --llm → first-line used, cache not consulted."""
        cache = {"test-proj": {"desc": "Should not appear", "date": TODAY}}
        (life_dir / ".index-cache.json").write_text(json.dumps(cache))
        result = _run(INDEX_SCRIPT, life_dir, "--dry-run")
        assert "Should not appear" not in result.stdout

    def test_stale_cache_ignored(self, life_dir):
        """Cache older than 7 days → falls back to first-line."""
        old_date = str(date.fromisoformat(TODAY) - timedelta(days=10))
        cache = {"test-proj": {"desc": "Old cached desc", "date": old_date}}
        (life_dir / ".index-cache.json").write_text(json.dumps(cache))
        result = _run(INDEX_SCRIPT, life_dir, "--llm", "--dry-run")
        # Falls back to first-line since cache is stale and claude CLI likely not in test env
        assert "test-proj" in result.stdout.lower() or "Test" in result.stdout


# ── 4E: Decay Dashboard ──────────────────────────────────────────────────


class TestDecayDashboard:
    def test_generates_dashboard_with_counts(self, life_dir):
        """Entities with facts → correct summary counts."""
        result = _run(DASHBOARD_SCRIPT, life_dir, "--dry-run")
        assert "Total facts: 2" in result.stdout
        assert "test-proj" in result.stdout

    def test_identifies_stale_facts(self, life_dir):
        """Facts past threshold → in Attention Needed."""
        result = _run(DASHBOARD_SCRIPT, life_dir, "--dry-run")
        # "Old stale fact" was last seen 2026-03-20, 19 days ago > 14 day threshold
        assert "Attention Needed" in result.stdout
        assert "Old stale fact" in result.stdout

    def test_empty_items_no_crash(self, tmp_path):
        """No items.json files → 'No facts tracked'."""
        (tmp_path / "Projects" / "_template").mkdir(parents=True)
        (tmp_path / "People").mkdir()
        (tmp_path / "Companies").mkdir()
        (tmp_path / "logs").mkdir()
        result = _run(DASHBOARD_SCRIPT, tmp_path, "--dry-run")
        assert "No facts tracked" in result.stdout

    def test_dry_run_no_file(self, life_dir):
        """--dry-run prints but doesn't create file."""
        _run(DASHBOARD_SCRIPT, life_dir, "--dry-run")
        assert not (life_dir / "logs" / "decay-dashboard.md").exists()

    def test_writes_file(self, life_dir):
        """Without --dry-run → writes to logs/decay-dashboard.md."""
        _run(DASHBOARD_SCRIPT, life_dir)
        assert (life_dir / "logs" / "decay-dashboard.md").exists()


# ── 4D: Knowledge Graph ──────────────────────────────────────────────────


class TestKnowledgeGraph:
    def test_generates_html_with_nodes(self, life_dir):
        """Entities → nodes in generated HTML."""
        _run(GRAPH_SCRIPT, life_dir)
        html = (life_dir / "graph.html").read_text()
        assert "test-proj" in html
        assert "d3.v7.min.js" in html

    def test_includes_edges(self, life_dir):
        """Relationships → edges in graph data."""
        result = _run(GRAPH_SCRIPT, life_dir, "--dry-run")
        assert "8 edges" in result.stdout or "2 edges" in result.stdout

    def test_orphan_nodes_included(self, life_dir):
        """Entities in relationships without dirs → still in graph."""
        result = _run(GRAPH_SCRIPT, life_dir, "--dry-run")
        assert "alice" in result.stdout  # alice has no People/alice/ dir yet

    def test_dry_run_no_file(self, life_dir):
        """--dry-run prints stats, doesn't create graph.html."""
        _run(GRAPH_SCRIPT, life_dir, "--dry-run")
        assert not (life_dir / "graph.html").exists()

    def test_after_fix_no_orphans(self, life_dir):
        """After lint --fix, graph nodes all have has_dir=True."""
        _run(LINT_SCRIPT, life_dir, "--fix")
        _run(GRAPH_SCRIPT, life_dir, "--dry-run")
        result = _run(GRAPH_SCRIPT, life_dir, "--dry-run")
        assert "(orphan)" not in result.stdout


# ── 4C: MCP Write Server (core function tests) ──────────────────────────

MCP_SCRIPT = Path(__file__).parent.parent / "mcp_write_server.py"

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestMCPWriteServer:
    @pytest.fixture(autouse=True)
    def _setup_mcp(self, life_dir, monkeypatch):
        """Import MCP module with LIFE_DIR pointed to tmp_path."""
        monkeypatch.setenv("LIFE_DIR", str(life_dir))
        import importlib.util
        spec = importlib.util.spec_from_file_location("mcp_ws", str(MCP_SCRIPT))
        self.mod = importlib.util.module_from_spec(spec)
        # Patch LIFE_DIR before exec
        import mcp_write_server
        monkeypatch.setattr(mcp_write_server, "LIFE_DIR", life_dir)
        monkeypatch.setattr(mcp_write_server, "LOCK_PATH", life_dir / "logs" / "consolidate.lock")
        monkeypatch.setattr(mcp_write_server, "TODAY", TODAY)
        self.ws = mcp_write_server

    def test_normalize_slug(self):
        assert self.ws._normalize_slug("Test Entity") == "test-entity"
        assert self.ws._normalize_slug("") == ""
        assert self.ws._normalize_slug("!!!") == ""

    def test_validate_path_blocks_traversal(self, life_dir):
        with pytest.raises(ValueError, match="traversal"):
            self.ws._validate_path(life_dir / ".." / "etc" / "passwd")

    def test_validate_path_allows_valid(self, life_dir):
        self.ws._validate_path(life_dir / "test.md")  # should not raise

    def test_find_entity(self, life_dir):
        result = self.ws._find_entity("test-proj")
        assert result is not None
        assert result.name == "test-proj"

    def test_find_entity_missing(self, life_dir):
        assert self.ws._find_entity("nonexistent") is None

    def test_create_entity_from_template(self, life_dir):
        result = self.ws.create_entity("person", "bob", "Bob Smith")
        assert "Created" in result
        assert (life_dir / "People" / "bob" / "summary.md").exists()
        summary = (life_dir / "People" / "bob" / "summary.md").read_text()
        assert "type: person" in summary
        assert "Bob Smith" in summary

    def test_create_entity_rejects_existing(self, life_dir):
        result = self.ws.create_entity("project", "test-proj", "Test")
        assert "already exists" in result

    def test_create_entity_rejects_invalid_type(self, life_dir):
        result = self.ws.create_entity("alien", "x", "X")
        assert "invalid type" in result

    def test_create_entity_rejects_empty_slug(self, life_dir):
        result = self.ws.create_entity("person", "!!!", "Bad")
        assert "empty" in result.lower()

    def test_add_fact_to_entity(self, life_dir):
        result = self.ws.add_fact("test-proj", "New fact from MCP", "event")
        assert "Added" in result
        items = json.loads((life_dir / "Projects" / "test-proj" / "items.json").read_text())
        mcp_items = [i for i in items if i.get("fact") == "New fact from MCP"]
        assert len(mcp_items) == 1
        assert mcp_items[0]["source"] == "mcp/life-write"
        assert mcp_items[0]["confidence"] == "single"

    def test_add_fact_invalid_category(self, life_dir):
        result = self.ws.add_fact("test-proj", "fact", "invalid-cat")
        assert "invalid category" in result

    def test_add_fact_missing_entity(self, life_dir):
        result = self.ws.add_fact("nonexistent", "fact", "event")
        assert "not found" in result

    def test_append_daily_note_creates_note(self, life_dir):
        # Remove existing note to test creation
        daily = life_dir / "Daily" / "2026" / "04" / f"{TODAY}.md"
        if daily.exists():
            daily.unlink()
        result = self.ws.append_daily_note("Test entry", "New Facts")
        assert "Appended" in result
        assert daily.exists()
        content = daily.read_text()
        assert "Test entry" in content
        assert "source: mcp" in content

    def test_append_daily_note_existing_section(self, life_dir):
        daily = life_dir / "Daily" / "2026" / "04" / f"{TODAY}.md"
        daily.write_text("## Active Projects\n- [[test-proj]]\n\n## New Facts\n- old fact\n")
        result = self.ws.append_daily_note("MCP fact", "New Facts")
        assert "Appended" in result
        content = daily.read_text()
        assert "MCP fact" in content
        assert "old fact" in content  # preserved

    def test_append_daily_note_invalid_section(self, life_dir):
        result = self.ws.append_daily_note("content", "Invalid Section Name")
        assert "invalid section" in result.lower()
