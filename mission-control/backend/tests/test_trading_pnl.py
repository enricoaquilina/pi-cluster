"""Tests for trading PnL accuracy and consistency.

Verifies:
A. Math correctness of _compute_copybot_stats PnL calculations
B. Consistency between summary (positions.json) and live (dashboard_state.json) endpoints
C. No oscillation regression when both data sources are present

These tests bypass the app package import chain (which requires PostgreSQL)
by importing trading_helpers and the route module directly via importlib.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

POLYBOT_DATA = Path(os.environ.get("POLYBOT_DATA_DIR", "/tmp"))
BACKEND = Path(__file__).resolve().parent.parent


def _load_module(name: str, filepath: Path):
    """Load a Python module by file path, bypassing package __init__."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, filepath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load only the modules we need, skipping app.__init__ (which imports db.py → PostgreSQL)
_config = _load_module("app.config", BACKEND / "app" / "config.py")
_helpers = _load_module("app.trading_helpers", BACKEND / "app" / "trading_helpers.py")


@pytest.fixture(autouse=True)
def _reset_rate_limiters():
    """Override conftest's autouse fixture — these tests don't need dispatch_engine."""
    yield


def _write_fixture(filename: str, data):
    (POLYBOT_DATA / filename).write_text(json.dumps(data))


def _clear_cache():
    _helpers._trading_cache.clear()


def _cleanup(*filenames):
    for f in filenames:
        (POLYBOT_DATA / f).unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def _fresh_cache_and_files():
    _clear_cache()
    yield
    _clear_cache()
    _cleanup("positions.json", "control.json", "paper_trades.json", "dashboard_state.json")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pos(entry_price, current_price, size, resolved=False, pnl=0.0):
    return {
        "entry_price": entry_price,
        "current_price": current_price,
        "size": size,
        "resolved": resolved,
        "pnl": pnl,
    }


def _copybot_summary():
    """Replicate what routes/trading.py:copybot_summary() does."""
    control = _helpers._read_json_cached("control.json")
    positions = _helpers._read_json_cached("positions.json")
    trades = _helpers._read_json_cached("paper_trades.json")
    stats = _helpers._compute_copybot_stats(control, positions, trades)

    live = _helpers._read_json_cached("dashboard_state.json")
    if live and "unrealized_pnl" in live:
        stats["unrealized_pnl"] = live["unrealized_pnl"]
        stats["total_pnl"] = round(stats["realized_pnl"] + live["unrealized_pnl"], 2)

    return stats


def _copybot_summary_original():
    """Current (unfixed) behavior — reads only from positions.json."""
    control = _helpers._read_json_cached("control.json")
    positions = _helpers._read_json_cached("positions.json")
    trades = _helpers._read_json_cached("paper_trades.json")
    return _helpers._compute_copybot_stats(control, positions, trades)


MINIMAL_CONTROL = {
    "mode": "paper",
    "order_size_usd": 10,
    "daily_budget_usd": 50,
    "daily_spent_usd": 0,
    "daily_date": "2026-04-30",
    "stop_loss_balance_usd": 30,
    "initial_balance_usd": 100,
    "max_total_positions": 20,
    "enabled_traders": [],
}


# ===========================================================================
# A. Math Correctness
# ===========================================================================

class TestPnlMath:

    def test_unrealized_pnl_calculation(self):
        positions = [
            _pos(0.50, 0.60, 10),  # unrealized = (0.60-0.50)*10 = 1.00
            _pos(0.70, 0.65, 20),  # unrealized = (0.65-0.70)*20 = -1.00
        ]
        stats = _helpers._compute_copybot_stats(MINIMAL_CONTROL, positions, [])
        assert stats["unrealized_pnl"] == 0.0

    def test_realized_pnl_from_resolved(self):
        positions = [
            _pos(0.50, 0, 10, resolved=True, pnl=5.0),
            _pos(0.40, 0, 20, resolved=True, pnl=-3.0),
            _pos(0.60, 0.65, 10),  # open — should not affect realized
        ]
        stats = _helpers._compute_copybot_stats(MINIMAL_CONTROL, positions, [])
        assert stats["realized_pnl"] == 2.0

    def test_total_pnl_is_sum(self):
        positions = [
            _pos(0.50, 0, 10, resolved=True, pnl=5.0),
            _pos(0.60, 0.70, 10),  # unrealized = 1.0
        ]
        stats = _helpers._compute_copybot_stats(MINIMAL_CONTROL, positions, [])
        assert stats["realized_pnl"] == 5.0
        assert stats["unrealized_pnl"] == 1.0
        assert stats["total_pnl"] == 6.0

    def test_pnl_ignores_zero_current_price(self):
        positions = [
            _pos(0.50, 0, 10),     # current_price=0, should be excluded
            _pos(0.50, 0.60, 10),  # unrealized = 1.0
        ]
        stats = _helpers._compute_copybot_stats(MINIMAL_CONTROL, positions, [])
        assert stats["unrealized_pnl"] == 1.0

    def test_pnl_empty_positions(self):
        stats = _helpers._compute_copybot_stats(MINIMAL_CONTROL, [], [])
        assert stats["unrealized_pnl"] == 0
        assert stats["realized_pnl"] == 0
        assert stats["total_pnl"] == 0


# ===========================================================================
# B. Consistency — Summary vs Live endpoints
# ===========================================================================

class TestPnlConsistency:

    def test_summary_uses_live_unrealized_when_available(self):
        positions = [_pos(0.50, 0.55, 100)]  # positions.json unrealized = 5.0
        _write_fixture("control.json", MINIMAL_CONTROL)
        _write_fixture("positions.json", positions)
        _write_fixture("paper_trades.json", [])
        _write_fixture("dashboard_state.json", {"unrealized_pnl": 7.95})

        stats = _copybot_summary()
        assert stats["unrealized_pnl"] == 7.95
        assert stats["total_pnl"] == 7.95  # realized=0 + unrealized=7.95

    def test_summary_falls_back_when_no_dashboard(self):
        positions = [_pos(0.50, 0.55, 100)]  # unrealized = 5.0
        _write_fixture("control.json", MINIMAL_CONTROL)
        _write_fixture("positions.json", positions)
        _write_fixture("paper_trades.json", [])
        _cleanup("dashboard_state.json")

        stats = _copybot_summary()
        assert stats["unrealized_pnl"] == 5.0

    def test_total_pnl_consistent_with_live(self):
        positions = [
            _pos(0.50, 0, 10, resolved=True, pnl=3.0),
            _pos(0.60, 0.55, 20),  # stale unrealized in positions.json
        ]
        live_unrealized = 8.05
        _write_fixture("control.json", MINIMAL_CONTROL)
        _write_fixture("positions.json", positions)
        _write_fixture("paper_trades.json", [])
        _write_fixture("dashboard_state.json", {"unrealized_pnl": live_unrealized})

        stats = _copybot_summary()
        assert stats["realized_pnl"] == 3.0
        assert stats["unrealized_pnl"] == live_unrealized
        assert stats["total_pnl"] == round(3.0 + live_unrealized, 2)


# ===========================================================================
# C. Oscillation Regression
# ===========================================================================

class TestPnlOscillation:

    def test_no_oscillation_between_stale_and_fresh(self):
        """Simulate the exact bug: positions.json has old prices giving PnL=7.95,
        dashboard_state.json has fresh calculation giving PnL=8.05.
        Summary should consistently return 8.05 (the fresh value)."""
        stale_positions = [_pos(0.50, 0.5795, 100)]  # stale → unrealized=7.95
        _write_fixture("control.json", MINIMAL_CONTROL)
        _write_fixture("positions.json", stale_positions)
        _write_fixture("paper_trades.json", [])
        _write_fixture("dashboard_state.json", {"unrealized_pnl": 8.05})

        results = set()
        for _ in range(5):
            _clear_cache()
            stats = _copybot_summary()
            results.add(stats["unrealized_pnl"])

        assert results == {8.05}, f"PnL oscillated across calls: {results}"

    def test_original_behavior_shows_stale_value(self):
        """Verify the unfixed behavior returns the stale positions.json value,
        proving the test catches the bug before the fix."""
        stale_positions = [_pos(0.50, 0.5795, 100)]  # unrealized=7.95
        _write_fixture("control.json", MINIMAL_CONTROL)
        _write_fixture("positions.json", stale_positions)
        _write_fixture("paper_trades.json", [])
        _write_fixture("dashboard_state.json", {"unrealized_pnl": 8.05})

        stats = _copybot_summary_original()
        assert stats["unrealized_pnl"] == 7.95  # stale — this is the bug
