"""Meta-tests that validate CI/CD configuration covers life-automation.

These tests read repo config files directly — no mocking, no external services.
They catch configuration drift (e.g., pre-commit hooks not covering new directories).
"""
import re
from pathlib import Path

# Resolve symlinks to find the actual repo root (~/life/scripts → ~/pi-cluster/life-automation)
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class TestPreCommitConfig:
    def test_ruff_covers_life_automation(self):
        """Ruff pre-commit hook files pattern should match life-automation/*.py."""
        config = (REPO_ROOT / ".pre-commit-config.yaml").read_text()
        # Find the ruff hook's files pattern
        assert "life-automation" in config, "life-automation not mentioned in .pre-commit-config.yaml"
        # Verify it's in the ruff files regex, not just a comment
        ruff_section = config[config.index("ruff"):]
        files_match = re.search(r"files:\s*(.+)", ruff_section)
        assert files_match, "No files: pattern found in ruff hook"
        assert "life-automation" in files_match.group(1)

    def test_shellcheck_covers_life_automation(self):
        """ShellCheck pre-commit hook files pattern should match life-automation/*.sh."""
        config = (REPO_ROOT / ".pre-commit-config.yaml").read_text()
        shellcheck_section = config[:config.index("ruff")]
        files_match = re.search(r"files:\s*(.+)", shellcheck_section)
        assert files_match, "No files: pattern found in shellcheck hook"
        assert "life-automation" in files_match.group(1)


class TestMakefile:
    def test_has_life_check_target(self):
        """Makefile should have a life-check target."""
        makefile = (REPO_ROOT / "Makefile").read_text()
        assert "life-check:" in makefile

    def test_validate_includes_life_check(self):
        """make validate should depend on life-check."""
        makefile = (REPO_ROOT / "Makefile").read_text()
        validate_line = [line for line in makefile.splitlines() if line.startswith("validate:")][0]
        assert "life-check" in validate_line


class TestCIWorkflow:
    def test_life_automation_workflow_exists(self):
        """life-automation.yml workflow should exist."""
        assert (REPO_ROOT / ".github/workflows/life-automation.yml").exists()

    def test_claude_fix_triggers_on_life_automation(self):
        """claude-fix.yml should trigger on Life Automation workflow."""
        config = (REPO_ROOT / ".github/workflows/claude-fix.yml").read_text()
        assert "Life Automation" in config

    def test_life_automation_uses_markers_not_ignore(self):
        """CI should use -m 'not local_only' for test filtering."""
        config = (REPO_ROOT / ".github/workflows/life-automation.yml").read_text()
        assert "not local_only" in config

    def test_ruff_toml_exists(self):
        """ruff.toml should exist as single source of lint config."""
        assert (REPO_ROOT / "ruff.toml").exists()

    def test_ruff_version_pinned_in_ci(self):
        """life-automation.yml should pin ruff version."""
        config = (REPO_ROOT / ".github/workflows/life-automation.yml").read_text()
        assert "ruff==" in config
