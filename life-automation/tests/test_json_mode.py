"""Tests for --json output mode in heartbeat_agent.py and backfill_history.py.

Validates that --json produces valid JSON on stdout with no progress line pollution.
Uses real subprocess invocations — no mocking.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HEARTBEAT_SCRIPT = Path(__file__).parent.parent / "heartbeat_agent.py"
BACKFILL_SCRIPT = Path(__file__).parent.parent / "backfill_history.py"


def _run_script(script: Path, life_dir: Path, extra_args: list[str] = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["LIFE_DIR"] = str(life_dir)
    env["MC_API_URL"] = "http://127.0.0.1:1"  # Unreachable — forces fast failure
    env["MC_API_KEY"] = ""
    env["TELEGRAM_TOKEN"] = ""
    env["TELEGRAM_CHAT_ID"] = ""
    cmd = [sys.executable, str(script), "--json", "--dry-run"]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)


class TestHeartbeatJsonMode:
    def test_json_output_is_valid(self, tmp_path):
        """--json --dry-run should produce valid JSON on stdout."""
        (tmp_path / "Daily/2026/04").mkdir(parents=True)
        (tmp_path / "logs").mkdir()
        r = _run_script(HEARTBEAT_SCRIPT, tmp_path)
        assert r.returncode == 0
        data = json.loads(r.stdout)  # Should not raise
        assert isinstance(data, dict)

    def test_no_progress_on_stdout(self, tmp_path):
        """stdout should not contain [agent] prefix lines."""
        (tmp_path / "Daily/2026/04").mkdir(parents=True)
        (tmp_path / "logs").mkdir()
        r = _run_script(HEARTBEAT_SCRIPT, tmp_path)
        assert r.returncode == 0
        for line in r.stdout.strip().splitlines():
            assert not line.startswith("[agent]"), f"Progress line leaked to stdout: {line}"

    def test_json_has_expected_keys(self, tmp_path):
        """JSON output should have timestamp, items_checked, actions."""
        (tmp_path / "Daily/2026/04").mkdir(parents=True)
        (tmp_path / "logs").mkdir()
        r = _run_script(HEARTBEAT_SCRIPT, tmp_path)
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert "timestamp" in data
        assert "items_checked" in data
        assert "actions" in data

    def test_progress_goes_to_stderr(self, tmp_path):
        """Progress lines should appear on stderr, not stdout."""
        (tmp_path / "Daily/2026/04").mkdir(parents=True)
        (tmp_path / "logs").mkdir()
        r = _run_script(HEARTBEAT_SCRIPT, tmp_path)
        assert r.returncode == 0
        assert "[agent]" in r.stderr


class TestBackfillJsonMode:
    def test_json_output_is_valid(self, tmp_path):
        """--json --dry-run should produce valid JSON on stdout."""
        (tmp_path / "Projects/pi-cluster").mkdir(parents=True)
        (tmp_path / "Projects/pi-cluster/items.json").write_text("[]")
        (tmp_path / "Projects/pi-cluster/summary.md").write_text("---\ntype: project\n---\n")
        (tmp_path / "People").mkdir()
        (tmp_path / "Companies").mkdir()
        (tmp_path / "relationships.json").write_text("[]")
        (tmp_path / "logs").mkdir()
        r = _run_script(BACKFILL_SCRIPT, tmp_path)
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert isinstance(data, dict)

    def test_no_progress_on_stdout(self, tmp_path):
        """stdout should not contain [backfill] prefix lines."""
        (tmp_path / "Projects/pi-cluster").mkdir(parents=True)
        (tmp_path / "Projects/pi-cluster/items.json").write_text("[]")
        (tmp_path / "Projects/pi-cluster/summary.md").write_text("---\ntype: project\n---\n")
        (tmp_path / "People").mkdir()
        (tmp_path / "Companies").mkdir()
        (tmp_path / "relationships.json").write_text("[]")
        (tmp_path / "logs").mkdir()
        r = _run_script(BACKFILL_SCRIPT, tmp_path)
        assert r.returncode == 0
        for line in r.stdout.strip().splitlines():
            assert not line.startswith("[backfill]"), f"Progress line leaked to stdout: {line}"
