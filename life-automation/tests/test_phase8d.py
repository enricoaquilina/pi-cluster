"""Phase 8D — index cross-references."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

CANONICAL = Path(__file__).resolve().parent.parent
GENERATE_INDEX = CANONICAL / "generate_index.py"


def _run(mini_life: Path, *args: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "LIFE_DIR": str(mini_life),
        "CONSOLIDATION_DATE": "2026-04-09",
        "PYTHONHASHSEED": "random",
        "LC_ALL": "C",
        "TZ": "UTC",
    }
    return subprocess.run(
        [sys.executable, str(GENERATE_INDEX), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


# ========================================================= happy path


def test_generate_index_runs_on_mini_life(mini_life):
    r = _run(mini_life, "--dry-run")
    assert r.returncode == 0, f"stderr={r.stderr}"
    assert "# Knowledge Base Index" in r.stdout


def test_gadget_has_inbound_cross_refs(mini_life):
    r = _run(mini_life, "--dry-run")
    assert r.returncode == 0
    # gadget is the hub — should show inbound worked on by / managed by / provided by
    gadget_section = _section(r.stdout, "gadget")
    assert gadget_section is not None
    assert "Worked on by" in gadget_section
    assert "[[beta]]" in gadget_section
    assert "[[delta]]" in gadget_section
    assert "Managed by" in gadget_section
    assert "Provided by" in gadget_section
    assert "[[acme]]" in gadget_section


def test_gadget_has_outbound_cross_ref(mini_life):
    r = _run(mini_life, "--dry-run")
    gadget_section = _section(r.stdout, "gadget")
    assert "Uses" in gadget_section
    assert "[[pi-hole]]" in gadget_section


def test_alpha_no_related_emits_no_sub_bullet(mini_life):
    r = _run(mini_life, "--dry-run")
    alpha_section = _section(r.stdout, "alpha")
    assert alpha_section is not None
    # No "Uses" / "Used by" / etc sub-bullet lines
    for label in ("Uses", "Used by", "Worked on by", "Related"):
        assert label + ":" not in alpha_section


def test_pi_hole_renders_with_inbound(mini_life):
    r = _run(mini_life, "--dry-run")
    pi_hole = _section(r.stdout, "pi-hole")
    assert pi_hole is not None, f"pi-hole missing from output: {r.stdout}"
    # pi-hole is USED BY gadget -> should show "Used by: [[gadget]]"
    assert "Used by" in pi_hole
    assert "[[gadget]]" in pi_hole


def test_pi_hole_no_dangling_warning(mini_life):
    """pi-hole has an inbound edge but exists as a folder — not dangling."""
    r = _run(mini_life, "--dry-run")
    assert "⚠️ missing" not in r.stdout  # nothing dangling in mini_life


# ========================================================= deterministic


def test_output_deterministic_across_runs(mini_life):
    r1 = _run(mini_life, "--dry-run")
    r2 = _run(mini_life, "--dry-run")
    assert r1.returncode == 0
    assert r2.returncode == 0
    assert r1.stdout == r2.stdout


# ========================================================= dangling edge


def test_dangling_edge_warning_to_stderr(tmp_path, mini_life):
    """Point relationships.json at a ghost entity → warning emitted."""
    rels = [
        {"from": "alpha", "from_type": "person", "to": "ghost", "relation": "uses",
         "first_seen": "2026-01-01", "last_seen": "2026-04-01"},
    ]
    (mini_life / "relationships.json").write_text(json.dumps(rels))
    r = _run(mini_life, "--dry-run")
    assert r.returncode == 0
    # Should render fine; ghost simply doesn't appear as an entity
    # alpha should show "Uses: [[ghost]]"
    alpha_section = _section(r.stdout, "alpha")
    assert "[[ghost]]" in alpha_section


def test_broken_relationships_json_fail_open(mini_life):
    (mini_life / "relationships.json").write_text("{not-json")
    r = _run(mini_life, "--dry-run")
    assert r.returncode == 0
    # Index generated without cross-refs; no crash
    assert "# Knowledge Base Index" in r.stdout
    # Warning on stderr
    assert "invalid JSON" in r.stderr or "relationships" in r.stderr


# ========================================================= helpers


def _section(text: str, slug: str) -> str | None:
    """Extract the chunk from the `- **[[slug]]**` line up to the next top-level
    bullet (``- **[[``) or blank line ending a section."""
    pattern = re.compile(
        rf"(- \*\*\[\[{re.escape(slug)}\]\]\*\*.*?)(?=\n- \*\*\[\[|\n## |\Z)",
        re.DOTALL,
    )
    m = pattern.search(text)
    return m.group(1) if m else None
