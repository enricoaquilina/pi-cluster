"""Phase 8A — orphan detection, kill switch, Fact schema v1, wiki scan."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

CANONICAL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CANONICAL))

from lint_knowledge_llm import (  # noqa: E402
    EXCLUDED_CONFIDENCE,
    _load_entity_facts,
    _scan_wiki_mentions,
    _strip_code_fences,
    find_orphans,
)

LINT_SCRIPT = CANONICAL / "lint_knowledge_llm.py"


def _run(mini_life: Path, *args: str, env_overrides=None) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "LIFE_DIR": str(mini_life),
        "CONSOLIDATION_DATE": "2026-04-09",
        "PYTHONHASHSEED": "random",
        "LC_ALL": "C",
        "TZ": "UTC",
    }
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(LINT_SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


# =========================================================== kill switch


def test_kill_switch_env_var_skips_llm(mini_life):
    """LIFE_LLM_DISABLED set -> exit 0 without any LLM work."""
    r = _run(mini_life, env_overrides={"LIFE_LLM_DISABLED": "1"})
    assert r.returncode == 0
    assert "skipping LLM work" in r.stderr


def test_kill_switch_sentinel_skips_llm(mini_life):
    (mini_life / ".llm-disabled").write_text("")
    r = _run(mini_life)
    assert r.returncode == 0
    assert "skipping LLM work" in r.stderr


def test_kill_switch_does_not_skip_orphans_only(mini_life):
    """--orphans mode runs even when LLM is disabled (no LLM required)."""
    r = _run(mini_life, "--orphans", "--json",
             env_overrides={"LIFE_LLM_DISABLED": "1"})
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert "orphan_structural" in data
    assert "pending_creation" in data


# ============================================================= Fact schema v1


def test_excluded_confidence_defaults_to_archived_only():
    assert EXCLUDED_CONFIDENCE == frozenset({"archived"})


def test_load_entity_facts_excludes_archived_only(tmp_path):
    items = [
        {"fact": "stale fact", "confidence": "stale", "date": "2026-01-01"},
        {"fact": "single fact", "confidence": "single", "date": "2026-01-01"},
        {"fact": "confirmed fact", "confidence": "confirmed", "date": "2026-01-01"},
        {"fact": "archived fact", "confidence": "archived", "date": "2026-01-01"},
    ]
    p = tmp_path / "items.json"
    p.write_text(json.dumps(items))
    active = _load_entity_facts(p)
    kinds = {f["confidence"] for f in active}
    assert kinds == {"stale", "single", "confirmed"}  # archived excluded


def test_load_entity_facts_env_override(tmp_path, monkeypatch):
    """LIFE_EXCLUDED_CONFIDENCE env var can broaden exclusion."""
    import importlib
    monkeypatch.setenv("LIFE_EXCLUDED_CONFIDENCE", "archived,stale")
    import lint_knowledge_llm as mod
    importlib.reload(mod)
    try:
        items = [
            {"fact": "stale fact", "confidence": "stale", "date": "2026-01-01"},
            {"fact": "confirmed fact", "confidence": "confirmed", "date": "2026-01-01"},
        ]
        p = tmp_path / "items.json"
        p.write_text(json.dumps(items))
        active = mod._load_entity_facts(p)
        assert all(f["confidence"] == "confirmed" for f in active)
    finally:
        monkeypatch.delenv("LIFE_EXCLUDED_CONFIDENCE", raising=False)
        importlib.reload(mod)


# ============================================================= wiki scan


def test_strip_code_fences_removes_fenced_blocks():
    md = "before\n```python\n[[pi-cluster]]\n```\nafter"
    out = _strip_code_fences(md)
    assert "[[pi-cluster]]" not in out
    assert "before" in out and "after" in out


def test_wiki_scan_mini_life_picks_up_mentions(mini_life):
    mentions = _scan_wiki_mentions(mini_life)
    assert "gadget" in mentions
    assert "alpha" in mentions
    assert "acme" in mentions
    assert "mission-control" in mentions  # pending entity reference


def test_wiki_scan_skips_code_fenced_widget(mini_life):
    """[[widget]] is inside a ``` fence in 2026-04-01.md and must NOT count."""
    mentions = _scan_wiki_mentions(mini_life)
    assert "widget" not in mentions, (
        f"widget wiki-link inside code fence should be skipped; got "
        f"files={mentions.get('widget')}"
    )


def test_wiki_scan_allowlists_md_only(mini_life):
    """sessions-digest-2026-04-02.jsonl contains [[pi-cluster]] but is NOT .md."""
    mentions = _scan_wiki_mentions(mini_life)
    # pi-cluster is only mentioned inside the .jsonl file — must not be picked up
    assert "pi-cluster" not in mentions


def test_wiki_scan_both_daily_namespaces(mini_life):
    """YYYY-MM-DD.md and maxwell-YYYY-MM-DD.md both scanned."""
    mentions = _scan_wiki_mentions(mini_life)
    # gadget appears in both 2026-04-01.md and maxwell-2026-04-02.md
    gadget_files = mentions["gadget"]
    assert len(gadget_files) >= 2
    basenames = {Path(f).name for f in gadget_files}
    assert "2026-04-01.md" in basenames
    assert "maxwell-2026-04-02.md" in basenames


# ============================================================= orphan categories


def test_find_orphans_returns_five_categories(mini_life):
    o = find_orphans(mini_life)
    expected = {
        "orphan_structural",
        "orphan_stale",
        "orphan_edge_only",
        "orphan_prose_only",
        "pending_creation",
    }
    assert set(o.keys()) == expected


def test_pi_hole_is_orphan_edge_only(mini_life):
    """pi-hole exists, has 0 facts, has inbound edge, no prose mentions."""
    o = find_orphans(mini_life)
    assert "pi-hole" in o["orphan_edge_only"]
    assert "pi-hole" not in o["orphan_structural"]  # has edge -> not structural


def test_mission_control_is_pending_creation(mini_life):
    """[[mission-control]] appears in daily notes but has no folder."""
    o = find_orphans(mini_life)
    assert "mission-control" in o["pending_creation"]
    # Not any other category — it has no folder
    for cat in ("orphan_structural", "orphan_stale", "orphan_edge_only", "orphan_prose_only"):
        assert "mission-control" not in o[cat]


def test_widget_is_orphan_structural(mini_life):
    """widget has no edges and its wiki-link is inside a code fence → no mentions."""
    o = find_orphans(mini_life)
    assert "widget" in o["orphan_structural"]


def test_gadget_is_not_orphan(mini_life):
    """gadget is the hub — it should not appear in ANY orphan category."""
    o = find_orphans(mini_life)
    for cat, items in o.items():
        assert "gadget" not in items, f"gadget wrongly in {cat}"


# ============================================================= orphan CLI


def test_orphans_cli_json_output(mini_life):
    r = _run(mini_life, "--orphans", "--json")
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert "pi-hole" in data["orphan_edge_only"]
    assert "mission-control" in data["pending_creation"]


def test_orphans_cli_writes_pending_log(mini_life):
    r = _run(mini_life, "--orphans")
    assert r.returncode == 0
    pending = mini_life / "logs" / "pending-entities.log"
    assert pending.exists()
    content = pending.read_text()
    assert "mission-control" in content


def test_orphans_cli_text_output(mini_life):
    r = _run(mini_life, "--orphans")
    assert r.returncode == 0
    # Should list the non-empty categories
    assert "pending_creation" in r.stdout
    assert "mission-control" in r.stdout
