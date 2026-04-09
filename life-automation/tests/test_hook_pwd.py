"""Phase 8B — hook PWD segment-match + exit-0 invariant.

Regression suite for the pre-v3 substring PWD matcher that misfired on
paths like ``/tmp/not-pi-cluster-backup``.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

CANONICAL = Path(__file__).resolve().parent.parent
HOOK = CANONICAL / "cc_start_hook.sh"
CONFIG = CANONICAL / "config" / "project-slugs.json"


def _run_hook(cwd: Path, life_dir: Path) -> subprocess.CompletedProcess:
    """Run the hook with a custom LIFE_DIR but keep the real HOME so the
    hook can locate its canonical ``config/project-slugs.json``."""
    env = {
        **os.environ,
        "LIFE_DIR": str(life_dir),
        "LIFE_GIT_SYNC_DISABLED": "1",
    }
    return subprocess.run(
        ["bash", str(HOOK)],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )


@pytest.fixture
def minimal_life(tmp_path):
    life = tmp_path / "life"
    (life / "logs").mkdir(parents=True)
    (life / "Projects" / "pi-cluster").mkdir(parents=True)
    (life / "Projects" / "pi-cluster" / "summary.md").write_text(
        "---\ntype: project\n---\n\nPi cluster project.\n"
    )
    # Hook looks for $LIFE_DIR/scripts/session_search.py; symlink to canonical
    (life / "scripts").symlink_to(CANONICAL)
    return life


# ============================================================ config exists


def test_project_slugs_config_exists():
    assert CONFIG.exists()


# ============================================================ segment match


def test_exact_segment_resolves_to_slug(tmp_path, minimal_life):
    cwd = tmp_path / "pi-cluster" / "sub"
    cwd.mkdir(parents=True)
    r = _run_hook(cwd, minimal_life)
    assert r.returncode == 0
    assert "Project Context: pi-cluster" in r.stdout


def test_nested_pi_cluster_life_automation(tmp_path, minimal_life):
    cwd = tmp_path / "pi-cluster" / "life-automation"
    cwd.mkdir(parents=True)
    r = _run_hook(cwd, minimal_life)
    assert r.returncode == 0
    assert "Project Context: pi-cluster" in r.stdout


# ============================================================ false positives


@pytest.mark.parametrize("bad", [
    "not-pi-cluster-backup",
    "pi-cluster-old",
    "pi-cluster.bak",
    "something/other",
])
def test_false_positive_substring_does_not_match(tmp_path, minimal_life, bad):
    cwd = tmp_path / bad
    cwd.mkdir(parents=True)
    r = _run_hook(cwd, minimal_life)
    assert r.returncode == 0
    assert "Project Context:" not in r.stdout, (
        f"{bad!r} should NOT trigger a project context section (pre-v3 regression)"
    )


# ========================================================== no config file


def test_unrelated_cwd_no_context(tmp_path, minimal_life):
    cwd = tmp_path / "unrelated"
    cwd.mkdir()
    r = _run_hook(cwd, minimal_life)
    assert r.returncode == 0
    assert "Project Context:" not in r.stdout


# ============================================================ exit-0 invariant


def test_hook_exits_zero_with_missing_life_dir(tmp_path):
    """If LIFE_DIR points nowhere, hook must still exit 0."""
    bogus = tmp_path / "not-a-life-dir"
    # Do NOT create it
    env = {
        **os.environ,
        "LIFE_DIR": str(bogus),
        "LIFE_GIT_SYNC_DISABLED": "1",
    }
    r = subprocess.run(
        ["bash", str(HOOK)],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert r.returncode == 0


def test_hook_exits_zero_with_empty_life_dir(tmp_path):
    """Minimal LIFE_DIR with nothing in it -> hook still exits 0."""
    life = tmp_path / "life"
    life.mkdir()
    env = {
        **os.environ,
        "LIFE_DIR": str(life),
        "LIFE_GIT_SYNC_DISABLED": "1",
    }
    r = subprocess.run(
        ["bash", str(HOOK)],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert r.returncode == 0
