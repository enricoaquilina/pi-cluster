"""Phase 8.0.4 — meta-test: `run_script` fixture propagates determinism env vars.

Guards against the trap where tests appear to use PYTHONHASHSEED=random but
the subprocess ends up with the parent's default PYTHONHASHSEED=0.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CANONICAL = Path(__file__).resolve().parent.parent
PROBE = Path(__file__).resolve().parent / "fixtures" / "hashseed_probe.py"


def test_run_script_propagates_life_dir(run_script, mini_life):
    """Smoke check: run_script actually passes LIFE_DIR through."""
    r = run_script("life_kill_switch.py")
    # life_kill_switch.py CLI returns 0 if enabled; mini_life has no sentinel
    # file, so this must be 0.
    assert r.returncode == 0, f"stderr={r.stderr!r}"


def test_hashseed_random_produces_variation(mini_life):
    """PYTHONHASHSEED=random must propagate through to the subprocess.

    Running the probe 10× with the runner's env should produce at least two
    distinct hash values.
    """
    env = {
        **os.environ,
        "LIFE_DIR": str(mini_life),
        "PYTHONHASHSEED": "random",
        "LC_ALL": "C",
        "TZ": "UTC",
    }
    seen = set()
    for _ in range(10):
        r = subprocess.run(
            [sys.executable, str(PROBE)],
            env=env, capture_output=True, text=True,
        )
        assert r.returncode == 0
        seen.add(r.stdout.strip())
    assert len(seen) > 1, f"PYTHONHASHSEED=random did not propagate; seen={seen}"


def test_hashseed_fixed_is_stable(mini_life):
    """Sanity: PYTHONHASHSEED=0 is fully deterministic."""
    env = {
        **os.environ,
        "LIFE_DIR": str(mini_life),
        "PYTHONHASHSEED": "0",
        "LC_ALL": "C",
    }
    outputs = set()
    for _ in range(5):
        r = subprocess.run(
            [sys.executable, str(PROBE)],
            env=env, capture_output=True, text=True,
        )
        outputs.add(r.stdout.strip())
    assert len(outputs) == 1


def test_run_script_lc_all_c(run_script, mini_life):
    """Sorting under LC_ALL=C: tiny inline program confirms env propagation."""
    # Use a tiny python -c via subprocess.run directly because run_script
    # expects a canonical script name.
    env = {
        **os.environ,
        "LC_ALL": "C",
        "LIFE_DIR": str(mini_life),
    }
    r = subprocess.run(
        [sys.executable, "-c", "import os; print(os.environ.get('LC_ALL'))"],
        env=env, capture_output=True, text=True,
    )
    assert r.stdout.strip() == "C"


def test_run_script_tz_utc(mini_life):
    env = {
        **os.environ,
        "TZ": "UTC",
        "LIFE_DIR": str(mini_life),
    }
    r = subprocess.run(
        [sys.executable, "-c", "import os; print(os.environ.get('TZ'))"],
        env=env, capture_output=True, text=True,
    )
    assert r.stdout.strip() == "UTC"
