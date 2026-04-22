#!/usr/bin/env python3
"""Tests for weekly_review_data.py"""
import json
import os
import sys
from pathlib import Path
from textwrap import dedent

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ["CONSOLIDATION_DATE"] = "2026-04-22"

from weekly_review_data import (
    parse_active_projects,
    parse_pending_items,
    count_wiki_mentions,
    detect_flags,
    extract_section,
)


class TestParseActiveProjects:
    def test_actual_note_format(self):
        """Real format used in daily notes: - [[slug]] — status"""
        content = dedent("""\
        ## Active Projects
        - [[polymarket-bot]] — 9 traders active, dynamic weights deployed
        - [[pi-cluster]] — Master thermal optimized (62→53°C)

        ## What We Worked On
        """)
        projects = parse_active_projects(content)
        assert len(projects) == 2
        assert projects[0]["slug"] == "polymarket-bot"
        assert "9 traders" in projects[0]["status_text"]
        assert projects[1]["slug"] == "pi-cluster"

    def test_bold_wikilink_format(self):
        """heartbeat_check.py format: - **[[slug]]**: status"""
        content = dedent("""\
        ## Active Projects
        - **[[pi-cluster]]**: Running well

        ## Next
        """)
        projects = parse_active_projects(content)
        assert len(projects) == 1
        assert projects[0]["slug"] == "pi-cluster"

    def test_bold_plain_format(self):
        """Plain bold: - **slug**: status"""
        content = dedent("""\
        ## Active Projects
        - **pi-cluster**: Running well

        ## Next
        """)
        projects = parse_active_projects(content)
        assert len(projects) == 1
        assert projects[0]["slug"] == "pi-cluster"

    def test_en_dash_separator(self):
        content = "## Active Projects\n- [[project]] – status text\n"
        projects = parse_active_projects(content)
        assert len(projects) == 1
        assert projects[0]["status_text"] == "status text"

    def test_hyphen_separator(self):
        content = "## Active Projects\n- [[project]] - status text\n"
        projects = parse_active_projects(content)
        assert len(projects) == 1

    def test_empty_section(self):
        content = "## Active Projects\n<!-- nothing -->\n## Next\n"
        assert parse_active_projects(content) == []

    def test_no_section(self):
        content = "## What We Worked On\n- stuff\n"
        assert parse_active_projects(content) == []

    def test_slug_lowercased(self):
        content = "## Active Projects\n- [[Pi-Cluster]] — running\n"
        projects = parse_active_projects(content)
        assert projects[0]["slug"] == "pi-cluster"


class TestParsePendingItems:
    def test_plain_format(self):
        """Actual format: - item text (no checkbox)"""
        content = dedent("""\
        ## Pending Items
        - Persist fan trip points on master
        - Monitor Telegram for clean messages
        - CLOB V2 migration deadline: April 28

        ## New Facts
        """)
        items = parse_pending_items(content)
        assert len(items) == 3
        assert "Persist fan" in items[0]
        assert "CLOB V2" in items[2]

    def test_checkbox_format(self):
        """carry_forward.py format: - [ ] item"""
        content = dedent("""\
        ## Pending Items
        - [ ] Do something
        - [x] Already done
        - [ ] Another thing

        ## Next
        """)
        items = parse_pending_items(content)
        assert len(items) == 2
        assert "Do something" in items[0]
        assert "Another thing" in items[1]

    def test_mixed_format(self):
        content = dedent("""\
        ## Pending Items
        - [ ] Checkbox item
        - Plain item
        - [x] Completed
        """)
        items = parse_pending_items(content)
        assert len(items) == 2

    def test_empty_section(self):
        content = "## Pending Items\n<!-- nothing -->\n## Next\n"
        assert parse_pending_items(content) == []

    def test_no_section(self):
        content = "## Active Projects\n- stuff\n"
        assert parse_pending_items(content) == []


class TestCountWikiMentions:
    def test_counts_correctly(self):
        content = "Working on [[pi-cluster]] today. Also [[pi-cluster]] config. And [[polymarket-bot]]."
        counts = count_wiki_mentions(content)
        assert counts["pi-cluster"] == 2
        assert counts["polymarket-bot"] == 1

    def test_case_insensitive(self):
        content = "[[Pi-Cluster]] and [[pi-cluster]]"
        counts = count_wiki_mentions(content)
        assert counts["pi-cluster"] == 2

    def test_no_mentions(self):
        content = "No wiki links here"
        assert len(count_wiki_mentions(content)) == 0


class TestDetectFlags:
    def test_blocked(self):
        assert "blocked" in detect_flags("Blocked on API key")

    def test_deadline(self):
        assert "has_deadline" in detect_flags("CLOB V2 deadline: April 28")

    def test_needs_coding(self):
        assert "needs_coding" in detect_flags("Needs coding session")

    def test_needs_attention(self):
        assert "needs_attention" in detect_flags("Needs attention on deploy")

    def test_clean_status(self):
        assert detect_flags("Running smoothly") == []


class TestExtractSection:
    def test_extracts_middle_section(self):
        content = dedent("""\
        ## First
        content1
        ## Second
        content2
        ## Third
        content3
        """)
        assert "content2" in extract_section(content, "Second")
        assert "content3" not in extract_section(content, "Second")

    def test_strips_comments(self):
        content = "## Test\n<!-- comment -->\nactual content\n"
        result = extract_section(content, "Test")
        assert "comment" not in result
        assert "actual" in result

    def test_last_section(self):
        content = "## First\ncontent1\n## Last\ncontent2\n"
        assert "content2" in extract_section(content, "Last")


class TestIntegration:
    def test_full_mode_json_output(self):
        """Run the actual script and verify JSON schema."""
        import subprocess
        result = subprocess.run(
            [sys.executable, str(Path(__file__).parent.parent / "weekly_review_data.py"),
             "--mode", "full"],
            capture_output=True, text=True,
            env={**os.environ, "CONSOLIDATION_DATE": "2026-04-22"},
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        data = json.loads(result.stdout)
        assert data["mode"] == "full"
        assert "active_projects" in data
        assert "pending_items" in data
        assert "entity_stats" in data
        assert "fact_health" in data
        assert "promotion_candidates" in data
        assert "orphan_entities" in data
        assert "projects_to_review" in data

    def test_daily_mode_json_output(self):
        """Daily mode should not include weekly-only fields."""
        import subprocess
        result = subprocess.run(
            [sys.executable, str(Path(__file__).parent.parent / "weekly_review_data.py"),
             "--mode", "daily"],
            capture_output=True, text=True,
            env={**os.environ, "CONSOLIDATION_DATE": "2026-04-22"},
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        data = json.loads(result.stdout)
        assert data["mode"] == "daily"
        assert "promotion_candidates" not in data
        assert "orphan_entities" not in data
        assert "projects_to_review" not in data
