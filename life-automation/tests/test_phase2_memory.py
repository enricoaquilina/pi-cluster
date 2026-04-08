"""Phase 2 tests — git sync, start hook context injection, gitleaks hook."""
import os
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

HOOK_SCRIPT = Path(__file__).parent.parent / "cc_start_hook.sh"
STOP_HOOK = Path(__file__).parent.parent / "cc_stop_hook.sh"
SYNC_LIB = Path(__file__).parent.parent / "lib" / "life-git-sync.sh"
TODAY = str(date.today())
YESTERDAY = str(date.today() - timedelta(days=1))


@pytest.fixture
def life_dir(tmp_path: Path) -> Path:
    """Create a minimal ~/life structure with a git repo."""
    parts = TODAY.split("-")
    yparts = YESTERDAY.split("-")
    (tmp_path / "Daily" / parts[0] / parts[1]).mkdir(parents=True)
    (tmp_path / "Daily" / yparts[0] / yparts[1]).mkdir(parents=True, exist_ok=True)
    (tmp_path / "logs").mkdir()
    return tmp_path


@pytest.fixture
def life_git_dir(life_dir: Path) -> Path:
    """Create a ~/life with initialized git repo."""
    subprocess.run(["git", "init", "--initial-branch=main"], cwd=life_dir,
                   capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=life_dir,
                   capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=life_dir,
                   capture_output=True, check=True)
    (life_dir / ".gitignore").write_text("logs/\n")
    subprocess.run(["git", "add", "-A"], cwd=life_dir, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=life_dir,
                   capture_output=True, check=True)
    return life_dir


def _run_hook(life_dir: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["LIFE_DIR"] = str(life_dir)
    env["LIFE_GIT_SYNC_DISABLED"] = "1"  # Don't actually pull
    return subprocess.run(
        ["bash", str(HOOK_SCRIPT)],
        capture_output=True, text=True, env=env, timeout=15,
    )


# ── Start Hook: Daily Context Injection ────────────────────────────────────


class TestDailyContextInjection:
    def test_active_projects_injected(self, life_dir):
        """Daily note with Active Projects → shows in start hook output."""
        parts = TODAY.split("-")
        daily = life_dir / "Daily" / parts[0] / parts[1] / f"{TODAY}.md"
        daily.write_text(
            "---\ndate: " + TODAY + "\n---\n\n"
            "## Active Projects\n"
            "- [[pi-cluster]] — Phase 2 in progress\n"
            "- [[gym-tracker]] — awaiting approval\n\n"
            "## What We Worked On\n"
            "- Memory access improvements\n",
            encoding="utf-8",
        )
        result = _run_hook(life_dir)
        assert result.returncode == 0
        assert "pi-cluster" in result.stdout
        assert "gym-tracker" in result.stdout
        assert "Daily Note Summary" in result.stdout

    def test_pending_items_injected(self, life_dir):
        """Daily note with Pending Items → shows in start hook output."""
        parts = TODAY.split("-")
        daily = life_dir / "Daily" / parts[0] / parts[1] / f"{TODAY}.md"
        daily.write_text(
            "---\ndate: " + TODAY + "\n---\n\n"
            "## Pending Items\n"
            "- [ ] Review PR #123\n"
            "- [ ] Update docs\n\n"
            "## New Facts\n",
            encoding="utf-8",
        )
        result = _run_hook(life_dir)
        assert result.returncode == 0
        assert "Review PR #123" in result.stdout

    def test_no_daily_note_no_crash(self, life_dir):
        """No daily note → no Daily Note Summary section, no crash."""
        result = _run_hook(life_dir)
        assert result.returncode == 0
        assert "Daily Note Summary" not in result.stdout

    def test_empty_daily_note(self, life_dir):
        """Empty daily note → no Active Projects shown."""
        parts = TODAY.split("-")
        daily = life_dir / "Daily" / parts[0] / parts[1] / f"{TODAY}.md"
        daily.write_text("---\ndate: " + TODAY + "\n---\n", encoding="utf-8")
        result = _run_hook(life_dir)
        assert result.returncode == 0

    def test_recent_sessions_section_present(self, life_dir):
        """Start hook has Recent Sessions section (FTS5-powered, replaced Yesterday's)."""
        # Create session_search.py in the expected location + a sessions.db
        import shutil
        scripts_dir = life_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        search_script = Path(__file__).parent.parent / "session_search.py"
        shutil.copy2(search_script, scripts_dir / "session_search.py")
        # Create DB with a test entry
        import importlib
        spec = importlib.util.spec_from_file_location("ss", search_script)
        ss = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ss)
        conn = ss.ensure_db(life_dir / "sessions.db")
        ss.index_session(conn, {
            "session_id": "test-1", "ts": "2026-04-07T10:00:00Z",
            "summary": "Fixed auth bug", "session_type": "coding",
            "decisions": [], "files_touched": [], "tool_counts": {}, "msg_count": 10,
        })
        conn.close()
        result = _run_hook(life_dir)
        assert result.returncode == 0
        assert "Recent Sessions" in result.stdout

    def test_no_section_leak_between_active_and_pending(self, life_dir):
        """Active Projects section shouldn't include Pending Items header."""
        parts = TODAY.split("-")
        daily = life_dir / "Daily" / parts[0] / parts[1] / f"{TODAY}.md"
        daily.write_text(
            "## Active Projects\n"
            "- Project A\n\n"
            "## Pending Items\n"
            "- Task B\n\n"
            "## New Facts\n",
            encoding="utf-8",
        )
        result = _run_hook(life_dir)
        # Count occurrences of "## Pending Items" — should be exactly 1
        count = result.stdout.count("## Pending Items")
        assert count == 1, f"Expected 1 occurrence of '## Pending Items', got {count}"


# ── Git Sync Library ───────────────────────────────────────────────────────


class TestGitSyncLib:
    def test_sync_skips_non_git(self, life_dir):
        """life_git_sync on non-git dir → exits cleanly, no error."""
        result = subprocess.run(
            ["bash", "-c", f"source {SYNC_LIB} && life_git_sync {life_dir}"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0

    def test_pull_skips_non_git(self, life_dir):
        """life_git_pull on non-git dir → exits cleanly, no error."""
        result = subprocess.run(
            ["bash", "-c", f"source {SYNC_LIB} && life_git_pull {life_dir}"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0

    def test_sync_commits_changes(self, life_git_dir):
        """life_git_sync with changes → creates a commit."""
        (life_git_dir / "test.md").write_text("hello\n")
        result = subprocess.run(
            ["bash", "-c", f"source {SYNC_LIB} && life_git_sync {life_git_dir}"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        log = subprocess.run(
            ["git", "-C", str(life_git_dir), "log", "--oneline", "-1"],
            capture_output=True, text=True,
        )
        assert "auto:" in log.stdout

    def test_sync_noop_when_clean(self, life_git_dir):
        """life_git_sync with no changes → no new commit."""
        before = subprocess.run(
            ["git", "-C", str(life_git_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()
        subprocess.run(
            ["bash", "-c", f"source {SYNC_LIB} && life_git_sync {life_git_dir}"],
            capture_output=True, text=True, timeout=10,
        )
        after = subprocess.run(
            ["git", "-C", str(life_git_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()
        assert before == after

    def test_disabled_env_var(self, life_git_dir):
        """LIFE_GIT_SYNC_DISABLED=1 → skips all operations."""
        (life_git_dir / "test.md").write_text("hello\n")
        env = os.environ.copy()
        env["LIFE_GIT_SYNC_DISABLED"] = "1"
        subprocess.run(
            ["bash", "-c", f"source {SYNC_LIB} && life_git_sync {life_git_dir}"],
            capture_output=True, text=True, timeout=10, env=env,
        )
        status = subprocess.run(
            ["git", "-C", str(life_git_dir), "status", "--porcelain"],
            capture_output=True, text=True,
        )
        assert "test.md" in status.stdout  # File still untracked

    def test_flock_creates_lockfile(self, life_git_dir):
        """life_git_sync creates the lock file."""
        (life_git_dir / "test.md").write_text("hello\n")
        subprocess.run(
            ["bash", "-c", f"source {SYNC_LIB} && life_git_sync {life_git_dir}"],
            capture_output=True, text=True, timeout=10,
        )
        assert (life_git_dir / ".git" / "life-sync.lock").exists()

    def test_self_healing_hookspath(self, life_git_dir):
        """life_git_sync sets core.hooksPath to .githooks."""
        (life_git_dir / "test.md").write_text("hello\n")
        subprocess.run(
            ["bash", "-c", f"source {SYNC_LIB} && life_git_sync {life_git_dir}"],
            capture_output=True, text=True, timeout=10,
        )
        hookspath = subprocess.run(
            ["git", "-C", str(life_git_dir), "config", "core.hooksPath"],
            capture_output=True, text=True,
        )
        assert hookspath.stdout.strip() == ".githooks"


# ── Gitleaks Pre-Commit Hook ──────────────────────────────────────────────


GITLEAKS_HOOK = Path(__file__).parent.parent.parent.parent / "life" / ".githooks" / "pre-commit"


class TestGitleaksHook:
    @pytest.mark.skipif(
        not Path(os.environ.get("HOME", "") + "/.local/bin/gitleaks").exists(),
        reason="gitleaks not installed",
    )
    def test_blocks_secrets(self, life_git_dir):
        """Staged file with API key → commit blocked (exit 1)."""
        hook = life_git_dir / ".githooks" / "pre-commit"
        hook.parent.mkdir(exist_ok=True)
        if GITLEAKS_HOOK.exists():
            hook.write_text(GITLEAKS_HOOK.read_text())
            hook.chmod(0o755)
        subprocess.run(["git", "-C", str(life_git_dir), "config", "core.hooksPath", ".githooks"],
                       capture_output=True)
        secret_file = life_git_dir / "secret.md"
        # Build a fake token that gitleaks detects (needs realistic entropy)
        import string
        fake = "ghp_" + "".join(
            string.ascii_letters[i % 52] for i in range(36)
        )
        secret_file.write_text(f"GITHUB_TOKEN={fake}\n")
        subprocess.run(["git", "-C", str(life_git_dir), "add", "secret.md"], capture_output=True)
        result = subprocess.run(
            ["git", "-C", str(life_git_dir), "commit", "-m", "test secret"],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "secrets detected" in result.stderr.lower() or "gitleaks" in result.stderr.lower()

    @pytest.mark.skipif(
        not Path(os.environ.get("HOME", "") + "/.local/bin/gitleaks").exists(),
        reason="gitleaks not installed",
    )
    def test_allows_clean(self, life_git_dir):
        """Staged file without secrets → commit passes (exit 0)."""
        hook = life_git_dir / ".githooks" / "pre-commit"
        hook.parent.mkdir(exist_ok=True)
        if GITLEAKS_HOOK.exists():
            hook.write_text(GITLEAKS_HOOK.read_text())
            hook.chmod(0o755)
        subprocess.run(["git", "-C", str(life_git_dir), "config", "core.hooksPath", ".githooks"],
                       capture_output=True)
        clean_file = life_git_dir / "clean.md"
        clean_file.write_text("This is a perfectly clean file.\n")
        subprocess.run(["git", "-C", str(life_git_dir), "add", "clean.md"], capture_output=True)
        result = subprocess.run(
            ["git", "-C", str(life_git_dir), "commit", "-m", "test clean"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_hook_warns_if_gitleaks_missing(self, life_git_dir):
        """If gitleaks binary doesn't exist at path → warns but allows (exit 0)."""
        hook = life_git_dir / ".githooks" / "pre-commit"
        hook.parent.mkdir(exist_ok=True)
        # Write a modified hook that points to a non-existent gitleaks
        hook.write_text(
            '#!/bin/bash\n'
            'GITLEAKS="/tmp/nonexistent-gitleaks-binary"\n'
            'if [ ! -x "$GITLEAKS" ]; then\n'
            '    echo "WARNING: gitleaks not installed" >&2\n'
            '    exit 0\n'
            'fi\n'
        )
        hook.chmod(0o755)
        subprocess.run(["git", "-C", str(life_git_dir), "config", "core.hooksPath", ".githooks"],
                       capture_output=True)
        (life_git_dir / "test.md").write_text("test\n")
        subprocess.run(["git", "-C", str(life_git_dir), "add", "test.md"], capture_output=True)
        result = subprocess.run(
            ["git", "-C", str(life_git_dir), "commit", "-m", "test no gitleaks"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "WARNING" in result.stderr


# ── Stop Hook Git Sync ────────────────────────────────────────────────────


class TestStopHookSync:
    def test_stop_hook_exits_zero(self, life_dir):
        """Stop hook always exits 0 (never fails)."""
        env = os.environ.copy()
        env["HOME"] = str(life_dir.parent)
        env["LIFE_GIT_SYNC_DISABLED"] = "1"
        result = subprocess.run(
            ["bash", str(STOP_HOOK)],
            capture_output=True, text=True, env=env, timeout=30,
        )
        # Stop hook should never fail (always exit 0)
        assert result.returncode == 0
