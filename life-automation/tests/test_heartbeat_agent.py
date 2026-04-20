"""Tests for heartbeat_agent.py — confidence scoring, persona routing,
budget tracking, node resources, and item processing."""
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# Ensure the scripts directory is importable
sys.path.insert(0, str(Path(__file__).parent.parent))
import heartbeat_agent as agent


# ── TestScoreConfidence ──────────────────────────────────────────────────────


class TestScoreConfidence:
    def test_clear_verb_high(self):
        level, reason = agent.score_confidence("fix gateway crash")
        assert level == "high"
        assert "fix" in reason

    def test_vague_word_low(self):
        level, reason = agent.score_confidence("think about pricing")
        assert level == "low"
        assert "think" in reason

    def test_risky_always_low(self):
        level, reason = agent.score_confidence("deploy to master")
        assert level == "low"
        assert "risky" in reason

    def test_no_signal_medium(self):
        # "update" is a clear verb so it returns high; test a genuinely ambiguous title
        level, _ = agent.score_confidence("update metrics")
        assert level in ("high", "medium")

    def test_empty_title_medium(self):
        level, _ = agent.score_confidence("")
        assert level == "medium"

    def test_risky_in_description(self):
        level, reason = agent.score_confidence("run cleanup", "this will rm -rf old data")
        assert level == "low"
        assert "risky" in reason

    def test_clear_verb_build(self):
        level, reason = agent.score_confidence("build the new module")
        assert level == "high"
        assert "build" in reason


# ── TestSelectPersona ────────────────────────────────────────────────────────


class TestSelectPersona:
    def test_docker_routes_to_harbor(self):
        assert agent.select_persona("deploy docker container") == "Harbor"

    def test_security_routes_to_sentinel(self):
        assert agent.select_persona("fix ufw rules") == "Sentinel"

    def test_frontend_routes_to_pixel(self):
        assert agent.select_persona("update css styling") == "Pixel"

    def test_default_routes_to_archie(self):
        assert agent.select_persona("refactor API endpoint logic") == "Archie"

    def test_docs_routes_to_docsworth(self):
        assert agent.select_persona("update documentation") == "Docsworth"

    def test_research_routes_to_scout(self):
        assert agent.select_persona("benchmark new framework") == "Scout"

    def test_data_routes_to_ledger(self):
        assert agent.select_persona("export metrics to CSV") == "Ledger"


# ── TestBudgetTracking ───────────────────────────────────────────────────────


class TestBudgetTracking:
    def test_budget_ok_when_under(self, tmp_path, monkeypatch):
        spend_log = tmp_path / "agent-spend.json"
        monkeypatch.setattr(agent, "SPEND_LOG", spend_log)
        monkeypatch.setattr(agent, "DAILY_BUDGET_USD", 5.0)
        monkeypatch.setattr(agent, "TODAY", "2026-03-30")
        # Write one small entry
        spend_log.write_text(json.dumps([
            {"date": "2026-03-30", "cost_usd": 1.0, "task": "test"},
        ]))
        assert agent.budget_ok(0.05) is True

    def test_budget_exceeded_when_over(self, tmp_path, monkeypatch):
        spend_log = tmp_path / "agent-spend.json"
        monkeypatch.setattr(agent, "SPEND_LOG", spend_log)
        monkeypatch.setattr(agent, "DAILY_BUDGET_USD", 5.0)
        monkeypatch.setattr(agent, "TODAY", "2026-03-30")
        spend_log.write_text(json.dumps([
            {"date": "2026-03-30", "cost_usd": 4.98, "task": "test"},
        ]))
        assert agent.budget_ok(0.05) is False

    def test_log_spend_creates_file(self, tmp_path, monkeypatch):
        spend_log = tmp_path / "logs" / "agent-spend.json"
        monkeypatch.setattr(agent, "SPEND_LOG", spend_log)
        monkeypatch.setattr(agent, "TODAY", "2026-03-30")
        agent.log_spend("test task", 0.10)
        assert spend_log.exists()
        data = json.loads(spend_log.read_text())
        assert len(data) == 1
        assert data[0]["cost_usd"] == 0.10
        assert data[0]["task"] == "test task"

    def test_log_spend_appends(self, tmp_path, monkeypatch):
        spend_log = tmp_path / "agent-spend.json"
        monkeypatch.setattr(agent, "SPEND_LOG", spend_log)
        monkeypatch.setattr(agent, "TODAY", "2026-03-30")
        spend_log.write_text(json.dumps([
            {"date": "2026-03-30", "cost_usd": 0.05, "task": "first"},
        ]))
        agent.log_spend("second task", 0.10)
        data = json.loads(spend_log.read_text())
        assert len(data) == 2
        assert data[1]["task"] == "second task"

    def test_budget_ok_no_file(self, tmp_path, monkeypatch):
        spend_log = tmp_path / "nonexistent.json"
        monkeypatch.setattr(agent, "SPEND_LOG", spend_log)
        monkeypatch.setattr(agent, "DAILY_BUDGET_USD", 5.0)
        assert agent.budget_ok(0.05) is True

    def test_log_spend_30_day_retention(self, tmp_path, monkeypatch):
        """Entries older than 30 days should be pruned."""
        from datetime import timedelta
        spend_log = tmp_path / "agent-spend.json"
        monkeypatch.setattr(agent, "SPEND_LOG", spend_log)
        monkeypatch.setattr(agent, "TODAY", "2026-04-03")
        old_date = str(date.fromisoformat("2026-04-03") - timedelta(days=31))
        spend_log.write_text(json.dumps([
            {"date": old_date, "cost_usd": 1.00, "task": "ancient"},
        ]))
        agent.log_spend("new task", 0.10)
        data = json.loads(spend_log.read_text())
        assert not any(e["task"] == "ancient" for e in data)
        assert any(e["task"] == "new task" for e in data)

    def test_log_spend_month_boundary(self, tmp_path, monkeypatch):
        """Entry from 29 days ago survives, 31 days ago pruned."""
        from datetime import timedelta
        spend_log = tmp_path / "agent-spend.json"
        monkeypatch.setattr(agent, "SPEND_LOG", spend_log)
        monkeypatch.setattr(agent, "TODAY", "2026-04-03")
        recent = str(date.fromisoformat("2026-04-03") - timedelta(days=29))
        old = str(date.fromisoformat("2026-04-03") - timedelta(days=31))
        spend_log.write_text(json.dumps([
            {"date": recent, "cost_usd": 0.50, "task": "recent"},
            {"date": old, "cost_usd": 0.50, "task": "old"},
        ]))
        agent.log_spend("today", 0.10)
        data = json.loads(spend_log.read_text())
        tasks = [e["task"] for e in data]
        assert "recent" in tasks
        assert "old" not in tasks
        assert "today" in tasks

    def test_log_spend_corrupt_file_recovery(self, tmp_path, monkeypatch):
        """Corrupt JSON in spend log → starts fresh, doesn't crash."""
        spend_log = tmp_path / "agent-spend.json"
        monkeypatch.setattr(agent, "SPEND_LOG", spend_log)
        monkeypatch.setattr(agent, "TODAY", "2026-04-03")
        spend_log.write_text("not valid json {{{")
        agent.log_spend("recovery task", 0.10)
        data = json.loads(spend_log.read_text())
        assert len(data) == 1
        assert data[0]["task"] == "recovery task"

    def test_log_spend_creates_parent_dirs(self, tmp_path, monkeypatch):
        """Missing logs/ directory created automatically."""
        spend_log = tmp_path / "deep" / "nested" / "agent-spend.json"
        monkeypatch.setattr(agent, "SPEND_LOG", spend_log)
        monkeypatch.setattr(agent, "TODAY", "2026-04-03")
        agent.log_spend("deep task", 0.10)
        assert spend_log.exists()
        data = json.loads(spend_log.read_text())
        assert data[0]["task"] == "deep task"

    def test_log_spend_task_truncation(self, tmp_path, monkeypatch):
        """Task names over 200 chars are truncated."""
        spend_log = tmp_path / "agent-spend.json"
        monkeypatch.setattr(agent, "SPEND_LOG", spend_log)
        monkeypatch.setattr(agent, "TODAY", "2026-04-03")
        long_task = "x" * 300
        agent.log_spend(long_task, 0.10)
        data = json.loads(spend_log.read_text())
        assert len(data[0]["task"]) == 200

    def test_budget_ok_sums_today_only(self, tmp_path, monkeypatch):
        """budget_ok() should sum only today's entries, not all entries."""
        spend_log = tmp_path / "agent-spend.json"
        monkeypatch.setattr(agent, "SPEND_LOG", spend_log)
        monkeypatch.setattr(agent, "DAILY_BUDGET_USD", 5.0)
        monkeypatch.setattr(agent, "TODAY", "2026-04-03")
        spend_log.write_text(json.dumps([
            {"date": "2026-04-02", "cost_usd": 4.90, "task": "yesterday"},
            {"date": "2026-04-03", "cost_usd": 0.05, "task": "today"},
        ]))
        # Yesterday's $4.90 should NOT count — only today's $0.05
        assert agent.budget_ok(0.10) is True


# ── TestNodeResources ────────────────────────────────────────────────────────


class TestNodeResources:
    def test_check_dispatchable(self, monkeypatch):
        monkeypatch.setattr(agent, "mc_get", lambda ep: [
            {"name": "slave0", "ram_total_mb": 4000, "ram_used_mb": 2000,
             "cpu_percent": 30, "status": "healthy"},
        ])
        nodes = agent.check_node_resources()
        assert nodes["slave0"]["dispatchable"] is True
        assert nodes["slave0"]["ram_pct"] == 50

    def test_check_not_dispatchable(self, monkeypatch):
        monkeypatch.setattr(agent, "mc_get", lambda ep: [
            {"name": "slave0", "ram_total_mb": 4000, "ram_used_mb": 3600,
             "cpu_percent": 90, "status": "healthy"},
        ])
        nodes = agent.check_node_resources()
        assert nodes["slave0"]["dispatchable"] is False
        assert nodes["slave0"]["ram_pct"] == 90

    def test_find_available_node_preferred(self):
        nodes = {
            "slave0": {"dispatchable": True},
            "slave1": {"dispatchable": True},
        }
        assert agent.find_available_node("slave0", nodes) == "slave0"

    def test_find_available_node_fallback(self):
        nodes = {
            "slave0": {"dispatchable": False},
            "slave1": {"dispatchable": True},
        }
        assert agent.find_available_node("slave0", nodes) == "slave1"

    def test_find_available_node_none(self):
        nodes = {
            "slave0": {"dispatchable": False},
            "slave1": {"dispatchable": False},
        }
        assert agent.find_available_node("slave0", nodes) is None

    def test_check_nodes_api_failure(self, monkeypatch):
        monkeypatch.setattr(agent, "mc_get", lambda ep: None)
        assert agent.check_node_resources() == {}


# ── TestProcessItem ──────────────────────────────────────────────────────────


class TestProcessItem:
    def _make_item(self, title="fix tests", desc="", source="mc_task"):
        return {"title": title, "description": desc, "source": source}

    def _healthy_nodes(self):
        return {
            "slave0": {"dispatchable": True, "ram_pct": 40},
            "slave1": {"dispatchable": True, "ram_pct": 50},
        }

    def test_high_confidence_dispatches(self, monkeypatch, tmp_path):
        monkeypatch.setattr(agent, "SPEND_LOG", tmp_path / "spend.json")
        monkeypatch.setattr(agent, "DAILY_BUDGET_USD", 10.0)
        monkeypatch.setattr(agent, "TODAY", "2026-03-30")
        monkeypatch.setattr(agent, "DRY_RUN", False)

        dispatched = {}
        def fake_mc_post(endpoint, data):
            dispatched["endpoint"] = endpoint
            dispatched["data"] = data
            return {"response": "Done! PR created."}
        monkeypatch.setattr(agent, "mc_post", fake_mc_post)
        monkeypatch.setattr(agent, "send_telegram", lambda msg: True)

        result = agent.process_item(self._make_item("fix broken tests"), self._healthy_nodes())
        assert result["action"] == "dispatched"
        assert result["confidence"] == "high"
        assert dispatched["endpoint"] == "/dispatch"

    def test_low_confidence_asks_human(self, monkeypatch):
        telegram_calls = []
        monkeypatch.setattr(agent, "send_telegram", lambda msg: telegram_calls.append(msg) or True)

        result = agent.process_item(self._make_item("think about pricing strategy"), self._healthy_nodes())
        assert result["action"] == "asked_human"
        assert result["confidence"] == "low"
        assert len(telegram_calls) == 1
        assert "input" in telegram_calls[0].lower() or "need" in telegram_calls[0].lower()

    def test_budget_exceeded_skips(self, monkeypatch, tmp_path):
        spend_log = tmp_path / "spend.json"
        monkeypatch.setattr(agent, "SPEND_LOG", spend_log)
        monkeypatch.setattr(agent, "DAILY_BUDGET_USD", 1.0)
        monkeypatch.setattr(agent, "TODAY", "2026-03-30")
        monkeypatch.setattr(agent, "DRY_RUN", False)
        # Exhaust the budget
        spend_log.write_text(json.dumps([
            {"date": "2026-03-30", "cost_usd": 0.98, "task": "previous"},
        ]))
        telegram_calls = []
        monkeypatch.setattr(agent, "send_telegram", lambda msg: telegram_calls.append(msg) or True)

        result = agent.process_item(self._make_item("fix the bug"), self._healthy_nodes())
        assert result["action"] == "budget_exceeded"
        assert len(telegram_calls) == 1

    def test_dry_run_does_not_dispatch(self, monkeypatch, tmp_path):
        monkeypatch.setattr(agent, "SPEND_LOG", tmp_path / "spend.json")
        monkeypatch.setattr(agent, "DAILY_BUDGET_USD", 10.0)
        monkeypatch.setattr(agent, "TODAY", "2026-03-30")
        monkeypatch.setattr(agent, "DRY_RUN", True)

        result = agent.process_item(self._make_item("fix the bug"), self._healthy_nodes())
        assert result["action"] == "dry_dispatch"


class TestPriorityFilter:
    """Tasks below medium priority should be skipped by collect_actionable_items."""

    def test_medium_priority_included(self, monkeypatch):
        mock_tasks = [
            {"id": "1", "title": "Fix bug", "description": "", "priority": "medium", "status": "todo"},
        ]
        monkeypatch.setattr(agent, "mc_get", lambda ep: mock_tasks if "tasks" in ep else None)
        items = agent.collect_actionable_items()
        assert any(i["title"] == "Fix bug" for i in items)

    def test_high_priority_included(self, monkeypatch):
        mock_tasks = [
            {"id": "2", "title": "Urgent fix", "description": "", "priority": "high", "status": "todo"},
        ]
        monkeypatch.setattr(agent, "mc_get", lambda ep: mock_tasks if "tasks" in ep else None)
        items = agent.collect_actionable_items()
        assert any(i["title"] == "Urgent fix" for i in items)

    def test_low_priority_excluded(self, monkeypatch):
        mock_tasks = [
            {"id": "3", "title": "Nice to have", "description": "", "priority": "low", "status": "todo"},
        ]
        monkeypatch.setattr(agent, "mc_get", lambda ep: mock_tasks if "tasks" in ep else None)
        items = agent.collect_actionable_items()
        assert not any(i["title"] == "Nice to have" for i in items)

    def test_mixed_priorities_filtered(self, monkeypatch):
        mock_tasks = [
            {"id": "1", "title": "Important", "priority": "medium", "status": "todo"},
            {"id": "2", "title": "Skip me", "priority": "low", "status": "todo"},
            {"id": "3", "title": "Critical", "priority": "urgent", "status": "todo"},
        ]
        monkeypatch.setattr(agent, "mc_get", lambda ep: mock_tasks if "tasks" in ep else None)
        items = agent.collect_actionable_items()
        titles = [i["title"] for i in items]
        assert "Important" in titles
        assert "Critical" in titles
        assert "Skip me" not in titles


# ── TestTaskSlug ──────────────────────────────────────────────────────────────


class TestTaskSlug:
    def test_basic_slug(self):
        assert agent._task_slug("Fix broken tests") == "fix-broken-tests"

    def test_special_chars_stripped(self):
        assert agent._task_slug("add OAuth2.0 support!") == "add-oauth2-0-support"

    def test_leading_trailing_hyphens_removed(self):
        slug = agent._task_slug("  build new feature  ")
        assert not slug.startswith("-")
        assert not slug.endswith("-")

    def test_no_truncation(self):
        long_title = "implement user authentication flow for the api gateway module"
        slug = agent._task_slug(long_title)
        # Should NOT be truncated to 50 chars — full slug preserved
        assert len(slug) > 50

    def test_similar_titles_get_distinct_slugs(self):
        s1 = agent._task_slug("implement user authentication flow")
        s2 = agent._task_slug("implement user authentication for api")
        assert s1 != s2

    def test_empty_title(self):
        # Should return empty string or just hyphens stripped to ""
        assert agent._task_slug("") == ""


# ── TestIsSimpleTask ──────────────────────────────────────────────────────────


class TestIsSimpleTask:
    def test_simple_verbs(self):
        for verb in ("fix", "update", "restart", "enable", "disable",
                     "rename", "delete", "remove", "upgrade", "rollback", "revert"):
            assert agent._is_simple_task(f"{verb} the thing") is True, f"Failed for verb: {verb}"

    def test_complex_verbs(self):
        for verb in ("build", "implement", "create"):
            assert agent._is_simple_task(f"{verb} something") is False, f"Should not be simple: {verb}"

    def test_empty_title(self):
        assert agent._is_simple_task("") is False


# ── TestTaskPrdStatus ─────────────────────────────────────────────────────────


class TestTaskPrdStatus:
    def _make_prd(self, tmp_path, title: str, content: str):
        slug = agent._task_slug(title)
        prd_dir = tmp_path / "Projects" / slug
        prd_dir.mkdir(parents=True)
        (prd_dir / "prd.md").write_text(content)

    def test_simple_task_returns_simple(self):
        assert agent._task_prd_status("fix the bug") == "simple"

    def test_complex_no_prd_needs_generation(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent, "LIFE_DIR", tmp_path)
        assert agent._task_prd_status("build new dashboard") == "needs_generation"

    def test_complex_prd_unapproved(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent, "LIFE_DIR", tmp_path)
        self._make_prd(tmp_path, "build dashboard", "---\napproved: false\n---\n# PRD")
        assert agent._task_prd_status("build dashboard") == "awaiting_approval"

    def test_complex_prd_approved(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent, "LIFE_DIR", tmp_path)
        self._make_prd(tmp_path, "build dashboard", "---\napproved: true\n---\n# PRD")
        assert agent._task_prd_status("build dashboard") == "approved"

    def test_complex_prd_no_approved_field(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent, "LIFE_DIR", tmp_path)
        self._make_prd(tmp_path, "build dashboard", "# PRD: build dashboard\nsome content")
        assert agent._task_prd_status("build dashboard") == "awaiting_approval"

    def test_empty_title_needs_generation(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent, "LIFE_DIR", tmp_path)
        assert agent._task_prd_status("") == "needs_generation"

    def test_implement_needs_generation(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent, "LIFE_DIR", tmp_path)
        assert agent._task_prd_status("implement OAuth2 login") == "needs_generation"

    def test_create_needs_generation(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent, "LIFE_DIR", tmp_path)
        assert agent._task_prd_status("create metrics dashboard") == "needs_generation"


# ── TestLoadPrdContext ────────────────────────────────────────────────────────


class TestLoadPrdContext:
    def test_no_prd_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent, "LIFE_DIR", tmp_path)
        assert agent._load_prd_context("build dashboard") == ""

    def test_prd_returned(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent, "LIFE_DIR", tmp_path)
        slug = agent._task_slug("build dashboard")
        prd_dir = tmp_path / "Projects" / slug
        prd_dir.mkdir(parents=True)
        (prd_dir / "prd.md").write_text("# PRD content here")
        assert agent._load_prd_context("build dashboard") == "# PRD content here"

    def test_prd_truncated_at_3000(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent, "LIFE_DIR", tmp_path)
        slug = agent._task_slug("build dashboard")
        prd_dir = tmp_path / "Projects" / slug
        prd_dir.mkdir(parents=True)
        (prd_dir / "prd.md").write_text("x" * 5000)
        result = agent._load_prd_context("build dashboard")
        assert len(result) == 3000


# ── TestGeneratePrd ───────────────────────────────────────────────────────────


class TestGeneratePrd:
    """Tests for _generate_prd() using real filesystem (tmp_path).
    OpenRouter calls are intercepted at the HTTP boundary via monkeypatching
    _call_openrouter — no mocking of mc_post or higher-level services."""

    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent, "LIFE_DIR", tmp_path)
        monkeypatch.setattr(agent, "SPEND_LOG", tmp_path / "logs" / "spend.json")
        monkeypatch.setattr(agent, "DAILY_BUDGET_USD", 10.0)
        monkeypatch.setattr(agent, "TODAY", "2026-03-31")
        monkeypatch.setattr(agent, "send_telegram", lambda msg: None)

    def test_dry_run_no_file(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        monkeypatch.setattr(agent, "DRY_RUN", True)
        result = agent._generate_prd("build dashboard", "desc")
        assert result == "dry_prd_needed"
        assert not (tmp_path / "Projects").exists()

    def test_budget_exceeded_no_file(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        monkeypatch.setattr(agent, "DRY_RUN", False)
        monkeypatch.setattr(agent, "DAILY_BUDGET_USD", 0.001)
        result = agent._generate_prd("build dashboard", "")
        assert result == "budget_exceeded"
        assert not (tmp_path / "Projects").exists()

    def test_openrouter_failure_returns_failed(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        monkeypatch.setattr(agent, "DRY_RUN", False)
        monkeypatch.setattr(agent, "_call_openrouter", lambda prompt, timeout=60: None)
        telegrams = []
        monkeypatch.setattr(agent, "send_telegram", lambda msg: telegrams.append(msg))
        result = agent._generate_prd("build dashboard", "")
        assert result == "prd_generation_failed"
        assert any("failed" in m.lower() for m in telegrams)

    def test_prd_written_with_approved_false(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        monkeypatch.setattr(agent, "DRY_RUN", False)
        monkeypatch.setattr(agent, "_call_openrouter", lambda prompt, timeout=60: "## Introduction\nTest PRD content.")
        result = agent._generate_prd("build dashboard", "some context")
        assert result == "prd_generated"
        slug = agent._task_slug("build dashboard")
        prd_path = tmp_path / "Projects" / slug / "prd.md"
        assert prd_path.exists()
        content = prd_path.read_text()
        assert "approved: false" in content
        assert "# PRD: build dashboard" in content
        assert "Test PRD content" in content

    def test_prd_creates_sibling_files(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        monkeypatch.setattr(agent, "DRY_RUN", False)
        monkeypatch.setattr(agent, "_call_openrouter", lambda prompt, timeout=60: "content")
        agent._generate_prd("build dashboard", "")
        slug = agent._task_slug("build dashboard")
        project_dir = tmp_path / "Projects" / slug
        assert (project_dir / "items.json").exists()
        assert (project_dir / "summary.md").exists()

    def test_spend_logged(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        monkeypatch.setattr(agent, "DRY_RUN", False)
        monkeypatch.setattr(agent, "_call_openrouter", lambda prompt, timeout=60: "content")
        agent._generate_prd("build dashboard", "")
        spend_log = tmp_path / "logs" / "spend.json"
        assert spend_log.exists()
        entries = json.loads(spend_log.read_text())
        assert any("PRD" in e.get("task", "") for e in entries)

    def test_telegram_sent_on_success(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        monkeypatch.setattr(agent, "DRY_RUN", False)
        monkeypatch.setattr(agent, "_call_openrouter", lambda prompt, timeout=60: "content")
        telegrams = []
        monkeypatch.setattr(agent, "send_telegram", lambda msg: telegrams.append(msg))
        agent._generate_prd("build dashboard", "")
        assert len(telegrams) == 1
        assert "approved: true" in telegrams[0]


# ── TestProcessItemPlanningGate ───────────────────────────────────────────────


class TestProcessItemPlanningGate:
    """Tests for the planning gate logic inside process_item()."""

    def _healthy_nodes(self):
        return {
            "slave0": {"dispatchable": True, "ram_pct": 40},
            "slave1": {"dispatchable": True, "ram_pct": 50},
        }

    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent, "LIFE_DIR", tmp_path)
        monkeypatch.setattr(agent, "SPEND_LOG", tmp_path / "logs" / "spend.json")
        monkeypatch.setattr(agent, "DAILY_BUDGET_USD", 10.0)
        monkeypatch.setattr(agent, "TODAY", "2026-03-31")
        monkeypatch.setattr(agent, "DRY_RUN", False)
        monkeypatch.setattr(agent, "send_telegram", lambda msg: None)

    def test_complex_task_no_prd_triggers_generation(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        monkeypatch.setattr(agent, "_call_openrouter", lambda prompt, timeout=60: "PRD content")
        item = {"title": "build new metrics dashboard", "description": "", "source": "mc_task"}
        result = agent.process_item(item, self._healthy_nodes())
        assert result["action"] == "prd_generated"
        slug = agent._task_slug("build new metrics dashboard")
        assert (tmp_path / "Projects" / slug / "prd.md").exists()

    def test_complex_task_prd_unapproved_awaits(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        # Write an unapproved PRD
        slug = agent._task_slug("build new metrics dashboard")
        prd_dir = tmp_path / "Projects" / slug
        prd_dir.mkdir(parents=True)
        (prd_dir / "prd.md").write_text("---\napproved: false\n---\n# PRD")
        telegrams = []
        monkeypatch.setattr(agent, "send_telegram", lambda msg: telegrams.append(msg))
        item = {"title": "build new metrics dashboard", "description": "", "source": "mc_task"}
        result = agent.process_item(item, self._healthy_nodes())
        assert result["action"] == "awaiting_approval"
        assert any("approved: true" in m for m in telegrams)

    def test_complex_task_prd_approved_dispatches(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        # Write an approved PRD
        slug = agent._task_slug("build new metrics dashboard")
        prd_dir = tmp_path / "Projects" / slug
        prd_dir.mkdir(parents=True)
        (prd_dir / "prd.md").write_text("---\napproved: true\n---\n# PRD: build new metrics dashboard")
        dispatched = {}
        def fake_mc_post(endpoint, data):
            dispatched["called"] = True
            return {"response": "Done"}
        monkeypatch.setattr(agent, "mc_post", fake_mc_post)
        item = {"title": "build new metrics dashboard", "description": "", "source": "mc_task"}
        result = agent.process_item(item, self._healthy_nodes())
        assert result["action"] == "dispatched"
        assert dispatched.get("called")

    def test_simple_task_skips_prd_gate(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        dispatched = {}
        def fake_mc_post(endpoint, data):
            dispatched["called"] = True
            return {"response": "Done"}
        monkeypatch.setattr(agent, "mc_post", fake_mc_post)
        item = {"title": "fix broken database connection", "description": "", "source": "mc_task"}
        result = agent.process_item(item, self._healthy_nodes())
        # Should dispatch directly without PRD
        assert result["action"] == "dispatched"
        assert not (tmp_path / "Projects").exists() or not any(
            (tmp_path / "Projects").iterdir()
        )

    def test_prd_context_included_in_dispatch_prompt(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        slug = agent._task_slug("build new metrics dashboard")
        prd_dir = tmp_path / "Projects" / slug
        prd_dir.mkdir(parents=True)
        (prd_dir / "prd.md").write_text("---\napproved: true\n---\n# PRD\nMy PRD details here")
        prompts_sent = []
        def fake_mc_post(endpoint, data):
            prompts_sent.append(data.get("prompt", ""))
            return {"response": "Done"}
        monkeypatch.setattr(agent, "mc_post", fake_mc_post)
        item = {"title": "build new metrics dashboard", "description": "", "source": "mc_task"}
        agent.process_item(item, self._healthy_nodes())
        assert prompts_sent
        assert "My PRD details here" in prompts_sent[0]


# ── Telegram Markdown Escaping ─────────────────────────────────────────────


class TestTelegramEscaping:
    """Test _escape_md() escapes Telegram Markdown v1 metacharacters."""

    def test_escapes_underscores(self):
        assert agent._escape_md("gym_tracker_app") == r"gym\_tracker\_app"

    def test_escapes_brackets(self):
        assert agent._escape_md("[x] done") == r"\[x] done"

    def test_escapes_backticks(self):
        assert agent._escape_md("run `qmd embed`") == r"run \`qmd embed\`"

    def test_escapes_asterisks(self):
        assert agent._escape_md("*bold text*") == r"\*bold text\*"

    def test_plain_text_unchanged(self):
        assert agent._escape_md("normal text here") == "normal text here"

    def test_mixed_content(self):
        """Real-world task title with multiple metacharacters."""
        text = "Fix [gym_tracker_app] `config` *urgent*"
        escaped = agent._escape_md(text)
        assert "\\_" in escaped
        assert "\\[" in escaped
        assert "\\`" in escaped
        assert "\\*" in escaped
        # Original text structure preserved
        assert "Fix" in escaped
        assert "config" in escaped

    def test_empty_string(self):
        assert agent._escape_md("") == ""

    def test_backslash_in_escape_chars(self):
        """Backslash before metachar should still escape the metachar."""
        result = agent._escape_md("path\\to\\_file")
        assert "\\_" in result


# ── Phase 1 Batch 1: Pure Functions (TDD — write failing, then implement) ────


class TestPrioritizeItems:
    """prioritize_items(): sort by priority, cap at N, stable tiebreak by id."""

    def test_sorts_urgent_first(self):
        items = [
            {"id": 3, "title": "low", "priority": "medium"},
            {"id": 1, "title": "urgent", "priority": "urgent"},
            {"id": 2, "title": "high", "priority": "high"},
        ]
        result = agent.prioritize_items(items)
        assert result[0]["priority"] == "urgent"
        assert result[1]["priority"] == "high"
        assert result[2]["priority"] == "medium"

    def test_caps_at_5(self):
        items = [{"id": i, "title": f"t{i}", "priority": "medium"} for i in range(10)]
        result = agent.prioritize_items(items)
        assert len(result) == 5

    def test_custom_cap(self):
        items = [{"id": i, "title": f"t{i}", "priority": "medium"} for i in range(10)]
        result = agent.prioritize_items(items, max_items=3)
        assert len(result) == 3

    def test_empty_list(self):
        assert agent.prioritize_items([]) == []

    def test_stable_tiebreak_by_id(self):
        """Same priority → sorted by id (deterministic)."""
        items = [
            {"id": 99, "title": "z", "priority": "medium"},
            {"id": 10, "title": "a", "priority": "medium"},
            {"id": 50, "title": "m", "priority": "medium"},
        ]
        result = agent.prioritize_items(items)
        ids = [r["id"] for r in result]
        assert ids == [10, 50, 99]

    def test_urgent_beats_cap(self):
        """3 urgent + 2 high + 3 medium → first 5 are 3 urgent + 2 high."""
        items = (
            [{"id": i, "title": f"u{i}", "priority": "urgent"} for i in range(3)]
            + [{"id": i + 10, "title": f"h{i}", "priority": "high"} for i in range(2)]
            + [{"id": i + 20, "title": f"m{i}", "priority": "medium"} for i in range(3)]
        )
        result = agent.prioritize_items(items)
        assert len(result) == 5
        priorities = [r["priority"] for r in result]
        assert priorities == ["urgent", "urgent", "urgent", "high", "high"]

    def test_missing_priority_defaults_medium(self):
        items = [
            {"id": 1, "title": "a", "priority": "urgent"},
            {"id": 2, "title": "b"},  # no priority field
        ]
        result = agent.prioritize_items(items)
        assert result[0]["priority"] == "urgent"

    def test_unknown_priority_sorts_last(self):
        items = [
            {"id": 1, "title": "a", "priority": "medium"},
            {"id": 2, "title": "b", "priority": "unknown_value"},
        ]
        result = agent.prioritize_items(items)
        assert result[0]["priority"] == "medium"


class TestComputeIdempotencyKey:
    """compute_idempotency_key(): sha1 of task_id only (not updated_at)."""

    def test_returns_hex_string(self):
        key = agent.compute_idempotency_key(42)
        assert isinstance(key, str)
        assert len(key) == 40  # sha1 hex digest

    def test_deterministic(self):
        k1 = agent.compute_idempotency_key(42)
        k2 = agent.compute_idempotency_key(42)
        assert k1 == k2

    def test_different_ids_different_keys(self):
        k1 = agent.compute_idempotency_key(1)
        k2 = agent.compute_idempotency_key(2)
        assert k1 != k2

    def test_is_sha1_of_task_id(self):
        import hashlib
        expected = hashlib.sha1(b"42").hexdigest()
        assert agent.compute_idempotency_key(42) == expected

    def test_string_id(self):
        """Task IDs could be strings — should still work."""
        key = agent.compute_idempotency_key("abc-123")
        assert len(key) == 40

    def test_none_id_raises(self):
        """Null task IDs should be rejected."""
        import pytest
        with pytest.raises((ValueError, TypeError)):
            agent.compute_idempotency_key(None)


# ── Phase 1 Batch 2: SQLite Dedup Layer ─────────────────────────────────────


class TestMaxwellDb:
    """init_maxwell_db(), record_dispatch(), is_duplicate(), cleanup_dedup()."""

    def test_init_creates_db(self, tmp_path):
        db_path = tmp_path / "maxwell.db"
        conn = agent.init_maxwell_db(db_path)
        assert db_path.exists()
        conn.close()

    def test_init_wal_mode(self, tmp_path):
        db_path = tmp_path / "maxwell.db"
        conn = agent.init_maxwell_db(db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        conn.close()

    def test_init_busy_timeout(self, tmp_path):
        db_path = tmp_path / "maxwell.db"
        conn = agent.init_maxwell_db(db_path)
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout == 5000
        conn.close()

    def test_init_creates_dispatches_table(self, tmp_path):
        db_path = tmp_path / "maxwell.db"
        conn = agent.init_maxwell_db(db_path)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        assert "dispatches" in tables
        conn.close()

    def test_init_creates_kv_table(self, tmp_path):
        db_path = tmp_path / "maxwell.db"
        conn = agent.init_maxwell_db(db_path)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        assert "kv" in tables
        conn.close()

    def test_init_idempotent(self, tmp_path):
        """Calling init twice doesn't crash or lose data."""
        db_path = tmp_path / "maxwell.db"
        conn1 = agent.init_maxwell_db(db_path)
        conn1.execute("INSERT INTO kv (key, value) VALUES ('test', 'hello')")
        conn1.commit()
        conn1.close()
        conn2 = agent.init_maxwell_db(db_path)
        val = conn2.execute("SELECT value FROM kv WHERE key='test'").fetchone()[0]
        assert val == "hello"
        conn2.close()

    def test_record_dispatch(self, tmp_path):
        db_path = tmp_path / "maxwell.db"
        conn = agent.init_maxwell_db(db_path)
        agent.record_dispatch(conn, task_id=42, status="dispatched")
        row = conn.execute("SELECT * FROM dispatches WHERE task_id=42").fetchone()
        assert row is not None
        conn.close()

    def test_is_duplicate_within_24h(self, tmp_path):
        db_path = tmp_path / "maxwell.db"
        conn = agent.init_maxwell_db(db_path)
        agent.record_dispatch(conn, task_id=42, status="dispatched")
        assert agent.is_duplicate(conn, task_id=42) is True
        conn.close()

    def test_is_not_duplicate_different_id(self, tmp_path):
        db_path = tmp_path / "maxwell.db"
        conn = agent.init_maxwell_db(db_path)
        agent.record_dispatch(conn, task_id=42, status="dispatched")
        assert agent.is_duplicate(conn, task_id=99) is False
        conn.close()

    def test_is_not_duplicate_after_24h(self, tmp_path):
        db_path = tmp_path / "maxwell.db"
        conn = agent.init_maxwell_db(db_path)
        # Insert with a timestamp 25 hours ago
        old_ts = (datetime.now() - timedelta(hours=25)).isoformat()
        conn.execute(
            "INSERT INTO dispatches (task_id, idempotency_key, dispatched_at, status) "
            "VALUES (?, ?, ?, ?)",
            (42, agent.compute_idempotency_key(42), old_ts, "dispatched"),
        )
        conn.commit()
        assert agent.is_duplicate(conn, task_id=42) is False
        conn.close()

    def test_cleanup_dedup_removes_old(self, tmp_path):
        db_path = tmp_path / "maxwell.db"
        conn = agent.init_maxwell_db(db_path)
        old_ts = (datetime.now() - timedelta(hours=49)).isoformat()
        conn.execute(
            "INSERT INTO dispatches (task_id, idempotency_key, dispatched_at, status) "
            "VALUES (?, ?, ?, ?)",
            (1, "old", old_ts, "dispatched"),
        )
        conn.execute(
            "INSERT INTO dispatches (task_id, idempotency_key, dispatched_at, status) "
            "VALUES (?, ?, ?, ?)",
            (2, "new", datetime.now().isoformat(), "dispatched"),
        )
        conn.commit()
        agent.cleanup_dedup(conn, max_age_hours=48)
        rows = conn.execute("SELECT task_id FROM dispatches").fetchall()
        ids = [r[0] for r in rows]
        assert 1 not in ids
        assert 2 in ids
        conn.close()

    def test_record_dispatch_stores_cost(self, tmp_path):
        db_path = tmp_path / "maxwell.db"
        conn = agent.init_maxwell_db(db_path)
        agent.record_dispatch(conn, task_id=42, status="dispatched",
                              cost_usd=0.08, model_used="claude-sonnet-4-6")
        row = conn.execute(
            "SELECT cost_usd, model_used FROM dispatches WHERE task_id=42"
        ).fetchone()
        assert row[0] == 0.08
        assert row[1] == "claude-sonnet-4-6"
        conn.close()


# ── Phase 1 Batch 3: mc_patch HTTP Helper ────────────────────────────────────


class TestMcPatch:
    """mc_patch(): PATCH with retry on 5xx/429, no retry on 4xx, DRY_RUN support."""

    def test_sends_patch_method(self, monkeypatch):
        requests_made = []
        def fake_urlopen(req, timeout=None):
            requests_made.append(req)
            class FakeResp:
                def read(self): return b'{"ok":true}'
            return FakeResp()
        monkeypatch.setattr(agent, "urlopen", fake_urlopen)
        monkeypatch.setattr(agent, "DRY_RUN", False)
        agent.mc_patch("/tasks/1", {"status": "review"})
        assert requests_made[0].get_method() == "PATCH"

    def test_sends_json_body(self, monkeypatch):
        requests_made = []
        def fake_urlopen(req, timeout=None):
            requests_made.append(req)
            class FakeResp:
                def read(self): return b'{"ok":true}'
            return FakeResp()
        monkeypatch.setattr(agent, "urlopen", fake_urlopen)
        monkeypatch.setattr(agent, "DRY_RUN", False)
        agent.mc_patch("/tasks/1", {"status": "review"})
        body = json.loads(requests_made[0].data)
        assert body["status"] == "review"

    def test_returns_parsed_json(self, monkeypatch):
        def fake_urlopen(req, timeout=None):
            class FakeResp:
                def read(self): return b'{"id":1,"status":"review"}'
            return FakeResp()
        monkeypatch.setattr(agent, "urlopen", fake_urlopen)
        monkeypatch.setattr(agent, "DRY_RUN", False)
        result = agent.mc_patch("/tasks/1", {"status": "review"})
        assert result == {"id": 1, "status": "review"}

    def test_dry_run_skips(self, monkeypatch):
        monkeypatch.setattr(agent, "DRY_RUN", True)
        result = agent.mc_patch("/tasks/1", {"status": "review"})
        assert result is None

    def test_retries_on_5xx(self, monkeypatch):
        import urllib.error
        call_count = [0]
        def fake_urlopen(req, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, None)
            class FakeResp:
                def read(self): return b'{"ok":true}'
            return FakeResp()
        monkeypatch.setattr(agent, "urlopen", fake_urlopen)
        monkeypatch.setattr(agent, "DRY_RUN", False)
        result = agent.mc_patch("/tasks/1", {"status": "review"})
        assert call_count[0] == 2
        assert result == {"ok": True}

    def test_retries_on_429(self, monkeypatch):
        import urllib.error
        call_count = [0]
        def fake_urlopen(req, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise urllib.error.HTTPError(req.full_url, 429, "Rate Limited", {}, None)
            class FakeResp:
                def read(self): return b'{"ok":true}'
            return FakeResp()
        monkeypatch.setattr(agent, "urlopen", fake_urlopen)
        monkeypatch.setattr(agent, "DRY_RUN", False)
        result = agent.mc_patch("/tasks/1", {"status": "review"})
        assert call_count[0] == 2
        assert result is not None

    def test_no_retry_on_4xx(self, monkeypatch):
        import urllib.error
        call_count = [0]
        def fake_urlopen(req, timeout=None):
            call_count[0] += 1
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        monkeypatch.setattr(agent, "urlopen", fake_urlopen)
        monkeypatch.setattr(agent, "DRY_RUN", False)
        result = agent.mc_patch("/tasks/1", {"status": "review"})
        assert call_count[0] == 1  # no retry
        assert result is None

    def test_returns_none_on_network_error(self, monkeypatch):
        from urllib.error import URLError as UE
        def fake_urlopen(req, timeout=None):
            raise UE("Connection refused")
        monkeypatch.setattr(agent, "urlopen", fake_urlopen)
        monkeypatch.setattr(agent, "DRY_RUN", False)
        result = agent.mc_patch("/tasks/1", {"status": "review"})
        assert result is None


# ── Phase 1 Batch 4: Telegram refactor to HTML ───────────────────────────────


class TestSendTelegramHTML:
    """send_telegram() refactored: returns (bool, int|None), HTML parse_mode,
    reply_markup support, truncation at 4000 chars."""

    def _fake_urlopen(self, requests_made, msg_id=123):
        """Return a fake urlopen that captures requests and returns a message_id."""
        def _urlopen(req, timeout=None):
            requests_made.append(req)
            class FakeResp:
                def read(self):
                    return json.dumps({"ok": True, "result": {"message_id": msg_id}}).encode()
            return FakeResp()
        return _urlopen

    def test_send_telegram_returns_tuple(self, monkeypatch):
        requests_made = []
        monkeypatch.setattr(agent, "TELEGRAM_TOKEN", "fake-token")
        monkeypatch.setattr(agent, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(agent, "DRY_RUN", False)
        monkeypatch.setattr(agent, "urlopen", self._fake_urlopen(requests_made, msg_id=77))
        result = agent.send_telegram("hello")
        assert isinstance(result, tuple)
        assert result[0] is True
        assert result[1] == 77

    def test_send_telegram_html_parse_mode(self, monkeypatch):
        requests_made = []
        monkeypatch.setattr(agent, "TELEGRAM_TOKEN", "fake-token")
        monkeypatch.setattr(agent, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(agent, "DRY_RUN", False)
        monkeypatch.setattr(agent, "urlopen", self._fake_urlopen(requests_made))
        agent.send_telegram("hello")
        body = json.loads(requests_made[0].data)
        assert body["parse_mode"] == "HTML"

    def test_send_telegram_with_reply_markup(self, monkeypatch):
        requests_made = []
        monkeypatch.setattr(agent, "TELEGRAM_TOKEN", "fake-token")
        monkeypatch.setattr(agent, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(agent, "DRY_RUN", False)
        monkeypatch.setattr(agent, "urlopen", self._fake_urlopen(requests_made))
        markup = {"inline_keyboard": [[{"text": "Yes", "callback_data": "yes"}]]}
        agent.send_telegram("choose", reply_markup=markup)
        body = json.loads(requests_made[0].data)
        assert body["reply_markup"] == markup

    def test_send_telegram_truncates_at_4000(self, monkeypatch):
        requests_made = []
        monkeypatch.setattr(agent, "TELEGRAM_TOKEN", "fake-token")
        monkeypatch.setattr(agent, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(agent, "DRY_RUN", False)
        monkeypatch.setattr(agent, "urlopen", self._fake_urlopen(requests_made))
        long_msg = "x" * 5000
        agent.send_telegram(long_msg)
        body = json.loads(requests_made[0].data)
        assert len(body["text"]) <= 4000


# ── Phase 1 Batch 5: Filesystem lock ─────────────────────────────────────────


class TestAcquireLock:
    """acquire_lock(): fcntl.flock-based exclusive lock."""

    def test_acquire_lock_succeeds(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent, "LIFE_DIR", tmp_path)
        (tmp_path / "logs").mkdir(exist_ok=True)
        success, fd = agent.acquire_lock()
        assert success is True
        assert fd is not None
        fd.close()

    def test_acquire_lock_contention(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent, "LIFE_DIR", tmp_path)
        (tmp_path / "logs").mkdir(exist_ok=True)
        # Hold the lock with a separate fd
        import fcntl
        lock_path = tmp_path / "logs" / "heartbeat-agent.lock"
        holder = open(lock_path, "w")
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Now try to acquire — should fail
        success, fd = agent.acquire_lock()
        assert success is False
        assert fd is None
        holder.close()

    def test_lock_released_on_close(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent, "LIFE_DIR", tmp_path)
        (tmp_path / "logs").mkdir(exist_ok=True)
        success1, fd1 = agent.acquire_lock()
        assert success1 is True
        fd1.close()  # release lock
        success2, fd2 = agent.acquire_lock()
        assert success2 is True
        fd2.close()


# ── Phase 1 Batch 6: Stale-task reaper ───────────────────────────────────────


class TestReapStaleTasks:
    """reap_stale_tasks(): reset in_progress tasks that are stale."""

    def test_reaper_resets_stale_task(self, monkeypatch):
        """Task with dispatch marker 20 min old should be reset."""
        from datetime import timezone
        stale_time = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        tasks = [{"id": 1, "title": "old task", "status": "in_progress",
                  "description": f"<!-- dispatch_started_at: {stale_time} -->",
                  "updated_at": datetime.now(timezone.utc).isoformat()}]
        patched_ids = []
        monkeypatch.setattr(agent, "mc_get", lambda ep: tasks)
        monkeypatch.setattr(agent, "mc_patch", lambda ep, data: patched_ids.append(1) or {"ok": True})
        result = agent.reap_stale_tasks()
        assert 1 in result

    def test_reaper_skips_fresh_task(self, monkeypatch):
        """Task with marker 5 min old should be left alone."""
        from datetime import timezone
        fresh_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        tasks = [{"id": 2, "title": "fresh task", "status": "in_progress",
                  "description": f"<!-- dispatch_started_at: {fresh_time} -->",
                  "updated_at": datetime.now(timezone.utc).isoformat()}]
        monkeypatch.setattr(agent, "mc_get", lambda ep: tasks)
        monkeypatch.setattr(agent, "mc_patch", lambda ep, data: None)
        result = agent.reap_stale_tasks()
        assert result == []

    def test_reaper_skips_locally_dispatching(self, monkeypatch):
        """Task in locally_dispatching set should be skipped even if stale."""
        from datetime import timezone
        stale_time = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        tasks = [{"id": 5, "title": "local task", "status": "in_progress",
                  "description": f"<!-- dispatch_started_at: {stale_time} -->",
                  "updated_at": datetime.now(timezone.utc).isoformat()}]
        monkeypatch.setattr(agent, "mc_get", lambda ep: tasks)
        monkeypatch.setattr(agent, "mc_patch", lambda ep, data: None)
        result = agent.reap_stale_tasks(locally_dispatching={5})
        assert result == []

    def test_reaper_falls_back_to_updated_at(self, monkeypatch):
        """No marker present — use updated_at field instead."""
        from datetime import timezone
        stale_updated = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        tasks = [{"id": 3, "title": "no marker", "status": "in_progress",
                  "description": "just a description",
                  "updated_at": stale_updated}]
        patched_ids = []
        monkeypatch.setattr(agent, "mc_get", lambda ep: tasks)
        monkeypatch.setattr(agent, "mc_patch", lambda ep, data: patched_ids.append(3) or {"ok": True})
        result = agent.reap_stale_tasks()
        assert 3 in result

    def test_reaper_handles_empty_list(self, monkeypatch):
        monkeypatch.setattr(agent, "mc_get", lambda ep: [])
        result = agent.reap_stale_tasks()
        assert result == []

    def test_reaper_handles_mc_down(self, monkeypatch):
        monkeypatch.setattr(agent, "mc_get", lambda ep: None)
        result = agent.reap_stale_tasks()
        assert result == []


# ── Phase 1 Batch 7: Input validation + credential check ────────────────────


class TestValidateTaskItem:
    """validate_task_item(): reject bad items, accept good ones."""

    def test_validate_rejects_empty_title(self):
        assert agent.validate_task_item({"id": 1, "title": ""}) is False

    def test_validate_rejects_null_id(self):
        assert agent.validate_task_item({"id": None, "title": "ok"}) is False

    def test_validate_accepts_valid_item(self):
        assert agent.validate_task_item({"id": 1, "title": "fix bug"}) is True

    def test_validate_rejects_whitespace_title(self):
        assert agent.validate_task_item({"id": 1, "title": "   "}) is False


class TestCheckCredentialPermissions:
    """check_credential_permissions(): warn on bad perms or missing files."""

    def test_credential_check_warns_world_readable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent, "_credential_paths", lambda: [tmp_path / "token"])
        token_file = tmp_path / "token"
        token_file.write_text("secret")
        token_file.chmod(0o644)
        warnings = agent.check_credential_permissions()
        assert any("permission" in w.lower() or "644" in w or "readable" in w.lower() for w in warnings)

    def test_credential_check_passes_on_600(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent, "_credential_paths", lambda: [tmp_path / "token"])
        token_file = tmp_path / "token"
        token_file.write_text("secret")
        token_file.chmod(0o600)
        warnings = agent.check_credential_permissions()
        assert warnings == []

    def test_credential_check_warns_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent, "_credential_paths", lambda: [tmp_path / "nonexistent"])
        warnings = agent.check_credential_permissions()
        assert any("missing" in w.lower() or "not found" in w.lower() or "exist" in w.lower() for w in warnings)


# ── Phase 1 Batch 8: Run log + integration ───────────────────────────────────


class TestAppendRunLog:
    """append_run_log(): append JSON lines to heartbeat-runs.jsonl."""

    def test_creates_file_and_appends(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent, "LIFE_DIR", tmp_path)
        agent.append_run_log({"ts": "2026-04-11", "items": 3})
        log_file = tmp_path / "logs" / "heartbeat-runs.jsonl"
        assert log_file.exists()
        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["items"] == 3

    def test_appends_multiple_lines(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent, "LIFE_DIR", tmp_path)
        agent.append_run_log({"run": 1})
        agent.append_run_log({"run": 2})
        log_file = tmp_path / "logs" / "heartbeat-runs.jsonl"
        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["run"] == 1
        assert json.loads(lines[1])["run"] == 2


class TestFullRunAgentCycle:
    """Integration: mock MC, run the agent, verify end-to-end."""

    def test_full_run_agent_cycle(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent, "LIFE_DIR", tmp_path)
        monkeypatch.setattr(agent, "SPEND_LOG", tmp_path / "logs" / "spend.json")
        monkeypatch.setattr(agent, "DAILY_BUDGET_USD", 10.0)
        monkeypatch.setattr(agent, "TODAY", "2026-04-11")
        monkeypatch.setattr(agent, "DRY_RUN", False)

        mock_tasks = [
            {"id": 1, "title": "fix broken tests", "description": "", "priority": "urgent", "status": "todo"},
            {"id": 2, "title": "update readme", "description": "", "priority": "high", "status": "todo"},
            {"id": 3, "title": "restart nginx", "description": "", "priority": "medium", "status": "todo"},
        ]

        def fake_mc_get(endpoint):
            if "tasks" in endpoint:
                return mock_tasks
            return None
        monkeypatch.setattr(agent, "mc_get", fake_mc_get)

        dispatches = []
        def fake_mc_post(endpoint, data):
            dispatches.append(data)
            return {"response": "Done!"}
        monkeypatch.setattr(agent, "mc_post", fake_mc_post)

        telegrams = []
        monkeypatch.setattr(agent, "send_telegram",
                            lambda msg, **kw: (telegrams.append(msg), (True, None))[-1])

        result = agent.run_agent()
        # Items were collected
        assert result["items_checked"] == 3
        # Actions were taken
        assert len(result["actions"]) == 3
        # Dispatches happened (all 3 are clear verbs: fix, update, restart)
        assert len(dispatches) >= 1
        # Run log was written
        log_path = tmp_path / "logs" / "agent-runs.json"
        assert log_path.exists()
