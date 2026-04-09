"""Shared pytest configuration for life-automation tests."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

CANONICAL = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
MINI_LIFE_SRC = FIXTURES / "mini-life"


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "local_only: test requires local tools (QMD, specific timezone, etc.)"
    )
    config.addinivalue_line(
        "markers", "slow: benchmarks and large-scale tests; skip in fast CI"
    )


# ---------------------------------------------------------------- mini_life


@pytest.fixture
def mini_life(tmp_path: Path) -> Path:
    """Deterministic copy of the mini-life fixture tree.

    Each test that asks for this fixture gets a fresh copy under tmp_path so
    mutations do not leak between tests.
    """
    if not MINI_LIFE_SRC.exists():
        pytest.skip("mini-life fixture missing; expected at tests/fixtures/mini-life")
    dst = tmp_path / "life"
    shutil.copytree(MINI_LIFE_SRC, dst)
    return dst


# ---------------------------------------------------------------- run_script


@pytest.fixture
def run_script(mini_life: Path):
    """Run a canonical-tree script as a subprocess with a deterministic env.

    Propagates:
      - LIFE_DIR pointed at the per-test mini_life copy
      - PYTHONHASHSEED=random so dict-ordering bugs surface
      - LC_ALL=C so sorted() is byte-wise deterministic
      - TZ=UTC so date.today() is stable across runners
    """
    def _run(script: str, *args: str, env_overrides: dict | None = None,
             timeout: float = 30.0) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "LIFE_DIR": str(mini_life),
            "PYTHONHASHSEED": "random",
            "LC_ALL": "C",
            "TZ": "UTC",
        }
        if env_overrides:
            env.update(env_overrides)
        return subprocess.run(
            [sys.executable, str(CANONICAL / script), *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    return _run
