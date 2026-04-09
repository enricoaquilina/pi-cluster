"""Phase 8.0.4 — invariants over mini-life and mini-life-bad fixtures."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MINI = FIXTURES / "mini-life"
BAD = FIXTURES / "mini-life-bad"

EXPECTED_VERSION = 1

# -------------------------------------------------------------- version file


def test_version_file_matches_constant():
    assert (MINI / "VERSION").read_text().strip() == str(EXPECTED_VERSION)


# -------------------------------------------------------------- mini_life invariants


@pytest.mark.parametrize("entity_path", [
    "People/alpha", "People/beta", "People/delta",
    "Companies/acme",
    "Projects/gadget", "Projects/widget", "Projects/pi-hole",
])
def test_mini_life_entities_exist(entity_path):
    ent = MINI / entity_path
    assert (ent / "summary.md").exists(), f"{entity_path}/summary.md missing"
    assert (ent / "items.json").exists(), f"{entity_path}/items.json missing"


@pytest.mark.parametrize("entity_path,expected_count", [
    ("People/alpha", 1),
    ("People/beta", 3),
    ("People/delta", 10),
    ("Companies/acme", 0),
    ("Projects/gadget", 5),
    ("Projects/widget", 4),
    ("Projects/pi-hole", 0),
])
def test_mini_life_fact_counts(entity_path, expected_count):
    items = json.loads((MINI / entity_path / "items.json").read_text())
    assert len(items) == expected_count


def test_mini_life_summaries_have_valid_frontmatter():
    """Every mini-life summary has parseable YAML frontmatter."""
    for summary in MINI.glob("*/*/summary.md"):
        text = summary.read_text()
        assert text.startswith("---\n")
        # Extract frontmatter
        parts = text.split("---\n", 2)
        assert len(parts) >= 3, f"{summary} missing closing fence"
        yaml.safe_load(parts[1])  # must not raise


def test_mini_life_relationships_json_parses():
    rels = json.loads((MINI / "relationships.json").read_text())
    assert isinstance(rels, list)
    assert len(rels) >= 5
    for edge in rels:
        for k in ("from", "to", "relation"):
            assert k in edge, f"edge missing {k}: {edge}"


def test_mini_life_pi_hole_regression():
    """Real-data regression case: pi-hole exists with empty items + inbound edge."""
    items = json.loads((MINI / "Projects" / "pi-hole" / "items.json").read_text())
    assert items == []
    rels = json.loads((MINI / "relationships.json").read_text())
    inbound = [e for e in rels if e["to"] == "pi-hole"]
    assert len(inbound) >= 1
    assert any(e["from"] == "gadget" and e["relation"] == "uses" for e in inbound)


def test_mini_life_mission_control_pending():
    """Real-data regression case: [[mission-control]] referenced without a folder."""
    assert not (MINI / "Projects" / "mission-control").exists()
    assert not (MINI / "People" / "mission-control").exists()
    daily_1 = (MINI / "Daily" / "2026" / "04" / "2026-04-01.md").read_text()
    assert "[[mission-control]]" in daily_1
    daily_2 = (MINI / "Daily" / "2026" / "04" / "2026-04-02.md").read_text()
    assert "[[mission-control]]" in daily_2


def test_mini_life_has_non_md_files_under_daily():
    """Plan v3 regression: Daily/ contains non-.md files that wiki scanner must skip."""
    digests = list((MINI / "Daily").rglob("sessions-digest-*.jsonl"))
    assert digests, "expected at least one sessions-digest-*.jsonl in fixture"
    text = digests[0].read_text()
    assert "[[pi-cluster]]" in text  # trap: must NOT be scanned


def test_mini_life_has_dual_daily_namespace():
    assert (MINI / "Daily" / "2026" / "04" / "2026-04-02.md").exists()
    assert (MINI / "Daily" / "2026" / "04" / "maxwell-2026-04-02.md").exists()


def test_mini_life_code_fence_exclusion():
    """Wiki-link regex must skip code-fenced mentions; ensure the fixture has one."""
    daily_1 = (MINI / "Daily" / "2026" / "04" / "2026-04-01.md").read_text()
    assert "```" in daily_1
    assert "[[widget]]" in daily_1  # inside a fence — should not count


# ---------------------------------------------------------- mini-life-bad


def test_mini_life_bad_corrupt_frontmatter_is_malformed():
    """Regression target: parser must NOT accept this file."""
    text = (BAD / "People" / "corrupt" / "summary.md").read_text()
    parts = text.split("---\n", 2)
    assert len(parts) >= 3
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(parts[1])


def test_mini_life_bad_relationships_is_invalid_json():
    raw = (BAD / "relationships.json").read_text()
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)


def test_mini_life_bad_contains_invalid_date_fact():
    items = json.loads((BAD / "People" / "baddate" / "items.json").read_text())
    assert any(f.get("date") == "2030-13-45" for f in items)
