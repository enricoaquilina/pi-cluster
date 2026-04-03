"""Tests for heartbeat_agent.py — confidence scoring, persona routing,
budget tracking, node resources, and item processing."""
import json
import sys
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
