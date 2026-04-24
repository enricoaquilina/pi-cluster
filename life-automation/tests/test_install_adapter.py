"""Tests for adapters/install_adapter.py — adapter management CLI."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapters"))
import install_adapter


@pytest.fixture
def manifest_dir(tmp_path):
    manifest = {
        "version": 1,
        "adapters": {
            "test-active": {
                "status": "active",
                "tier": "mcp",
                "description": "Test active adapter",
                "setup": ["echo installed"],
                "config_files": [],
                "hooks": ["test-hook.sh"],
            },
            "test-future": {
                "status": "future",
                "tier": "mcp",
                "description": "Test future adapter",
                "setup": [],
                "config_files": [],
            },
        },
    }
    mf = tmp_path / "manifest.json"
    mf.write_text(json.dumps(manifest))
    with patch.object(install_adapter, "MANIFEST", mf):
        with patch.object(install_adapter, "ADAPTERS_DIR", tmp_path):
            yield tmp_path


class TestList:
    def test_lists_all_adapters(self, manifest_dir, capsys):
        install_adapter.cmd_list(None)
        out = capsys.readouterr().out
        assert "test-active" in out
        assert "test-future" in out
        assert "active" in out
        assert "future" in out


class TestCheck:
    def test_check_known_adapter(self, manifest_dir, capsys):
        args = type("Args", (), {"adapter": "test-active"})()
        install_adapter.cmd_check(args)
        out = capsys.readouterr().out
        assert "test-active" in out
        assert "active" in out
        assert "test-hook.sh" in out

    def test_check_unknown_exits(self, manifest_dir):
        args = type("Args", (), {"adapter": "nonexistent"})()
        with pytest.raises(SystemExit, match="1"):
            install_adapter.cmd_check(args)


class TestInstall:
    def test_install_active_runs_setup(self, manifest_dir):
        args = type("Args", (), {"adapter": "test-active", "dry_run": False})()
        with patch.object(subprocess, "run") as mock_run:
            install_adapter.cmd_install(args)
            mock_run.assert_called_once()
            assert "echo installed" in mock_run.call_args[0][0]

    def test_install_dry_run_skips_commands(self, manifest_dir):
        args = type("Args", (), {"adapter": "test-active", "dry_run": True})()
        with patch.object(subprocess, "run") as mock_run:
            install_adapter.cmd_install(args)
            mock_run.assert_not_called()

    def test_install_future_prints_message(self, manifest_dir, capsys):
        args = type("Args", (), {"adapter": "test-future", "dry_run": False})()
        install_adapter.cmd_install(args)
        out = capsys.readouterr().out
        assert "future" in out

    def test_install_unknown_exits(self, manifest_dir):
        args = type("Args", (), {"adapter": "nope", "dry_run": False})()
        with pytest.raises(SystemExit, match="1"):
            install_adapter.cmd_install(args)
