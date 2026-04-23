"""Phase 8.0.1 — canonical-is-runtime topology invariants.

On this node, ~/life/scripts is a SYMLINK to ~/pi-cluster/life-automation.
If anyone replaces the symlink with a real directory the deploy topology has
drifted and nightly-consolidate.sh must refuse to run.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

CANONICAL = Path(__file__).resolve().parent.parent
LIFE_DIR = Path.home() / "life"
NIGHTLY = CANONICAL / "nightly-consolidate.sh"


# ---------------------------------------------------------------- symlink live


@pytest.mark.skipif(not LIFE_DIR.exists(), reason="~/life/ not present")
@pytest.mark.skipif(sys.platform == "win32", reason="symlink semantics differ")
def test_life_scripts_is_symlink():
    scripts = LIFE_DIR / "scripts"
    assert scripts.is_symlink(), (
        f"{scripts} is not a symlink; plan v3 8.0.1 requires canonical-is-runtime."
    )


@pytest.mark.skipif(not LIFE_DIR.exists(), reason="~/life/ not present")
@pytest.mark.skipif(sys.platform == "win32", reason="symlink semantics differ")
def test_life_scripts_points_into_canonical():
    scripts = LIFE_DIR / "scripts"
    resolved = scripts.resolve()
    # Accept any path that ends in pi-cluster/life-automation (node-independent)
    assert resolved.name == "life-automation" and resolved.parent.name == "pi-cluster", (
        f"~/life/scripts resolves to {resolved}; expected a pi-cluster/life-automation dir."
    )


# -------------------------------------------------------- nightly drift refuse


@pytest.mark.skipif(not NIGHTLY.exists(), reason="nightly-consolidate.sh missing")
def test_nightly_refuses_when_scripts_is_not_symlink(tmp_path, monkeypatch):
    """Simulate drift: LIFE_DIR with scripts/ as a real directory -> exit 1."""
    fake_life = tmp_path / "life"
    (fake_life / "scripts").mkdir(parents=True)  # real dir, not a symlink
    (fake_life / "logs").mkdir()

    # Run nightly-consolidate with fake LIFE_DIR. It will likely exit early on
    # other grounds, but we expect it to hit the topology check first with
    # exit code 1 and the "not a symlink" message.
    env = {
        **os.environ,
        "HOME": str(tmp_path),  # redirect ~/life to our fake tree
        "PATH": os.environ.get("PATH", ""),
    }
    result = subprocess.run(
        ["bash", str(NIGHTLY)],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    # topology check runs very early; confirm it fired
    combined = result.stdout + result.stderr
    assert "not a symlink" in combined or "topology has drifted" in combined, (
        f"expected topology error, got stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert result.returncode == 1


# ------------------------------------------------------- nightly kill-switch ok


def test_nightly_script_mentions_kill_switch():
    """Smoke check: nightly honors LIFE_LLM_DISABLED (via lib or inline)."""
    nightly_text = NIGHTLY.read_text()
    lib_file = CANONICAL / "lib" / "life-automation-lib.sh"
    lib_text = lib_file.read_text() if lib_file.exists() else ""
    combined = nightly_text + lib_text
    assert "LIFE_LLM_DISABLED" in combined
    assert ".llm-disabled" in combined
