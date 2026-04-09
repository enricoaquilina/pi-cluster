"""Phase 8C — rewrite_summaries hardening tests.

Covers: kill switch, Fact schema v1, protected flag, versioned backups,
self-amplification invariant (prompt contains only facts, not body).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

CANONICAL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CANONICAL))

import rewrite_summaries as rw  # noqa: E402

REWRITE_SCRIPT = CANONICAL / "rewrite_summaries.py"


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
        [sys.executable, str(REWRITE_SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


# ===================================================== kill switch


def test_kill_switch_env_var(mini_life):
    # Make delta auto_maintained so there would otherwise be work to do
    delta = mini_life / "People" / "delta" / "summary.md"
    delta.write_text(
        "---\ntype: person\nstatus: active\nauto_maintained: true\n---\n"
    )
    r = _run(mini_life, env_overrides={"LIFE_LLM_DISABLED": "1"})
    assert r.returncode == 0
    assert "skipping LLM work" in r.stderr
    # No rewrite happened
    assert (mini_life / "People" / "delta" / "summary.md").read_text().startswith("---\n")


def test_kill_switch_sentinel(mini_life):
    (mini_life / ".llm-disabled").write_text("")
    r = _run(mini_life, "--dry-run")
    assert r.returncode == 0
    assert "skipping LLM work" in r.stderr


# ===================================================== Fact schema v1


def test_excluded_confidence_defaults_to_archived_only():
    assert rw.EXCLUDED_CONFIDENCE == frozenset({"archived"})


def test_load_facts_includes_stale_and_single(tmp_path):
    entity = tmp_path / "entity"
    entity.mkdir()
    (entity / "items.json").write_text(json.dumps([
        {"fact": "stale fact", "confidence": "stale"},
        {"fact": "single fact", "confidence": "single"},
        {"fact": "confirmed fact", "confidence": "confirmed"},
        {"fact": "archived fact", "confidence": "archived"},
    ]))
    active = rw._load_facts(entity)
    kinds = {f["confidence"] for f in active}
    assert kinds == {"stale", "single", "confirmed"}


# ===================================================== protected flag


def test_protected_flag_detected(tmp_path):
    p = tmp_path / "summary.md"
    p.write_text("---\ntype: project\nprotected: true\n---\n")
    assert rw._is_protected(p) is True


def test_unprotected_flag_not_detected(tmp_path):
    p = tmp_path / "summary.md"
    p.write_text("---\ntype: project\n---\n")
    assert rw._is_protected(p) is False


# ================================ Step 0.5 YAML parser regression (Agent 1 P0)


def test_protected_false_when_only_in_body(tmp_path):
    """The pre-v3 regex matched body text; the YAML parser must not."""
    p = tmp_path / "summary.md"
    p.write_text(
        "---\ntype: project\n---\n\n"
        "# body\n\n"
        "Last week we set protected: true on this file but then reverted it.\n"
    )
    assert rw._is_protected(p) is False


def test_auto_maintained_false_when_only_in_body(tmp_path):
    """Substring match in pre-v3 false-matched prose like this."""
    p = tmp_path / "summary.md"
    p.write_text(
        "---\ntype: project\n---\n\n"
        "Note: auto_maintained: true was set experimentally last week.\n"
    )
    assert rw._is_auto_maintained(p) is False


def test_protected_true_with_inline_comment(tmp_path):
    """YAML comments are valid — regex rejected them."""
    p = tmp_path / "summary.md"
    p.write_text("---\ntype: project\nprotected: true  # hand-curated\n---\n")
    assert rw._is_protected(p) is True


def test_protected_false_for_quoted_string(tmp_path):
    """protected: \"true\" is a string, not a bool — must NOT protect."""
    p = tmp_path / "summary.md"
    p.write_text('---\ntype: project\nprotected: "true"\n---\n')
    assert rw._is_protected(p) is False


def test_auto_maintained_honors_yaml_alt_boolean(tmp_path):
    """YAML parses `yes` as True — honour it."""
    p = tmp_path / "summary.md"
    p.write_text("---\ntype: project\nauto_maintained: yes\n---\n")
    assert rw._is_auto_maintained(p) is True


def test_handles_bom(tmp_path):
    """UTF-8 BOM prefix must not break frontmatter detection."""
    p = tmp_path / "summary.md"
    p.write_bytes("\ufeff---\ntype: project\nprotected: true\n---\n".encode("utf-8"))
    assert rw._is_protected(p) is True


def test_handles_crlf(tmp_path):
    """CRLF line endings must not break the frontmatter regex anchor."""
    p = tmp_path / "summary.md"
    p.write_bytes(b"---\r\ntype: project\r\nprotected: true\r\n---\r\n")
    assert rw._is_protected(p) is True


def test_malformed_yaml_fails_closed(tmp_path):
    """Malformed YAML must return False (fail-closed), not raise."""
    p = tmp_path / "summary.md"
    p.write_text("---\ntype: : broken\nprotected: true\n---\n")
    # Either the YAML parser raises internally (returning {}) or it parses
    # but the structure is malformed; either way both helpers must be False
    # because the dict does not have a clean {protected: True} entry.
    assert rw._is_protected(p) is False
    assert rw._is_auto_maintained(p) is False


def test_missing_frontmatter_returns_false(tmp_path):
    p = tmp_path / "summary.md"
    p.write_text("# just a body\n\nno frontmatter at all.\n")
    assert rw._is_protected(p) is False
    assert rw._is_auto_maintained(p) is False


def test_protected_overrides_auto_maintained_in_dry_run(mini_life):
    """Entity with both flags set -> skipped."""
    delta = mini_life / "People" / "delta" / "summary.md"
    delta.write_text(
        "---\ntype: person\nstatus: active\n"
        "auto_maintained: true\nprotected: true\n"
        "---\n"
    )
    r = _run(mini_life, "--dry-run")
    assert r.returncode == 0
    assert "SKIP protected" in r.stdout
    assert "DRY RUN" not in r.stdout or "delta" not in r.stdout.split("SKIP protected")[1] if "SKIP protected" in r.stdout else True


def test_auto_maintained_alone_is_eligible_dry_run(mini_life):
    """auto_maintained: true (no protected) + enough facts -> dry-run lists it."""
    delta = mini_life / "People" / "delta" / "summary.md"
    delta.write_text(
        "---\ntype: person\nstatus: active\nauto_maintained: true\n---\n"
    )
    r = _run(mini_life, "--dry-run")
    assert r.returncode == 0
    assert "delta" in r.stdout


# ===================================================== self-amplification invariant


def test_rewrite_prompt_contains_no_body(mini_life, monkeypatch):
    """The prompt MUST NOT include the current summary body.

    Rationale: a poisoned prior rewrite must not self-propagate. We verify by
    capturing the exact argv the subprocess would be called with.
    """
    delta = mini_life / "People" / "delta" / "summary.md"
    poison = "UNIQUE_POISON_TOKEN_abc123 that should never reach the LLM"
    delta.write_text(
        "---\ntype: person\nstatus: active\nauto_maintained: true\n---\n"
        f"# delta\n\n{poison}\n\n## Open Questions\n- something\n"
    )

    captured: dict = {}

    def fake_run(argv, capture_output, text, timeout):
        captured["argv"] = argv
        class FakeResult:
            returncode = 1  # force no-write path
            stdout = ""
            stderr = ""
        return FakeResult()

    monkeypatch.setattr(rw.subprocess, "run", fake_run)
    monkeypatch.setattr(rw, "DRY_RUN", False)
    rw.rewrite_entity(delta)

    # The prompt is the -p argument; join everything and verify the poison isn't in it
    assert "argv" in captured, "subprocess.run should have been called"
    joined = " ".join(captured["argv"])
    assert poison not in joined, (
        "Self-amplification bug: rewrite prompt leaked the current summary body"
    )


# ===================================================== versioned backup


def test_rewrite_creates_versioned_backup(mini_life, monkeypatch):
    """When a rewrite proceeds, backup.snapshot() produces a timestamped .bak."""
    delta = mini_life / "People" / "delta" / "summary.md"
    delta.write_text(
        "---\ntype: person\nstatus: active\nauto_maintained: true\n---\n"
        "# delta\n\noriginal body\n"
    )

    # Stub LLM to return a fake valid-looking rewrite so we complete the happy path
    def fake_run(argv, capture_output, text, timeout):
        class FakeResult:
            returncode = 0
            stdout = "## What Matters Right Now\n- ok\n\n## Key Facts\n- fact\n"
            stderr = ""
        return FakeResult()

    monkeypatch.setattr(rw.subprocess, "run", fake_run)
    monkeypatch.setattr(rw, "DRY_RUN", False)
    rw.rewrite_entity(delta)

    # A timestamped backup must exist next to the summary
    backups = list(delta.parent.glob("summary.md.bak.*"))
    assert len(backups) >= 1, f"expected versioned backup, got {list(delta.parent.iterdir())}"
    # Old-style single-slot .bak should NOT exist
    old_style = delta.parent / "summary.md.bak"
    assert not old_style.exists() or not old_style.is_file() or old_style in backups


def test_no_rewrite_no_backup(mini_life):
    """Dry-run produces no backup files."""
    delta = mini_life / "People" / "delta" / "summary.md"
    delta.write_text(
        "---\ntype: person\nstatus: active\nauto_maintained: true\n---\n"
    )
    _run(mini_life, "--dry-run")
    backups = list(delta.parent.glob("*.bak*"))
    assert backups == []


# ===================================================== CLI smoke tests


def test_dry_run_no_auto_maintained_entities(mini_life):
    """Stock mini_life has no auto_maintained entities → nothing rewritten."""
    r = _run(mini_life, "--dry-run")
    assert r.returncode == 0
    assert "No entities eligible" in r.stdout or "0 entities rewritten" in r.stdout


def test_dry_run_lists_eligible_entity(mini_life):
    (mini_life / "Projects" / "gadget" / "summary.md").write_text(
        "---\ntype: project\nstatus: active\nauto_maintained: true\n---\n"
    )
    r = _run(mini_life, "--dry-run")
    assert r.returncode == 0
    assert "gadget" in r.stdout
