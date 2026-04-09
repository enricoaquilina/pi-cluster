"""Phase 8.0.3 — backup.py tests."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

CANONICAL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CANONICAL))

import backup  # noqa: E402


@pytest.fixture
def life_tree(tmp_path, monkeypatch):
    """Isolated LIFE_DIR so symlink-escape checks have a meaningful boundary."""
    life = tmp_path / "life"
    life.mkdir()
    monkeypatch.setenv("LIFE_DIR", str(life))
    return life


# ========================================================== basic snapshots


def test_single_snapshot_creates_file(life_tree):
    f = life_tree / "alpha.md"
    f.write_text("original content")
    bp = backup.snapshot(f)
    assert bp.exists()
    assert bp.read_text() == "original content"
    assert f.read_text() == "original content"  # original unchanged


def test_snapshot_returns_path_in_same_dir(life_tree):
    f = life_tree / "notes" / "a.md"
    f.parent.mkdir()
    f.write_text("x")
    bp = backup.snapshot(f)
    assert bp.parent == f.parent
    assert bp.name.startswith("a.md.bak.")


def test_empty_file_snapshot(life_tree):
    f = life_tree / "empty.md"
    f.write_text("")
    bp = backup.snapshot(f)
    assert bp.exists()
    assert bp.stat().st_size == 0


def test_unicode_content_round_trip(life_tree):
    f = life_tree / "u.md"
    content = "héllo 世界 🙂"
    f.write_text(content, encoding="utf-8")
    bp = backup.snapshot(f)
    assert bp.read_text(encoding="utf-8") == content


def test_missing_source_raises(life_tree):
    with pytest.raises(FileNotFoundError):
        backup.snapshot(life_tree / "nope.md")


# ============================================================== retention


def test_retention_keeps_n_newest(life_tree, monkeypatch):
    f = life_tree / "r.md"
    f.write_text("v0")
    # Control the timestamp to create distinct backups
    ts_iter = iter(["20260101-000000", "20260101-000001", "20260101-000002",
                    "20260101-000003", "20260101-000004", "20260101-000005"])
    monkeypatch.setattr(backup, "_timestamp", lambda: next(ts_iter))
    for _ in range(6):
        backup.snapshot(f, keep=5)
    backups = backup.list_backups(f)
    assert len(backups) == 5
    # Should have the 5 newest
    ts_suffixes = [b.name.split(".bak.")[-1] for b in backups]
    assert ts_suffixes[0] == "20260101-000005"  # newest first
    assert "20260101-000000" not in ts_suffixes  # oldest trimmed


def test_retention_per_file(life_tree, monkeypatch):
    """Two different files each keep their own set of backups."""
    a = life_tree / "a.md"
    b = life_tree / "b.md"
    a.write_text("a")
    b.write_text("b")
    counter = [0]
    def ts():
        counter[0] += 1
        return f"20260101-{counter[0]:06d}"
    monkeypatch.setattr(backup, "_timestamp", ts)
    for _ in range(6):
        backup.snapshot(a, keep=5)
        backup.snapshot(b, keep=5)
    assert len(backup.list_backups(a)) == 5
    assert len(backup.list_backups(b)) == 5


# ============================================================== collisions


def test_same_second_collision_gets_suffix(life_tree, monkeypatch):
    f = life_tree / "x.md"
    f.write_text("body")
    monkeypatch.setattr(backup, "_timestamp", lambda: "20260101-120000")
    bp1 = backup.snapshot(f)
    bp2 = backup.snapshot(f)
    bp3 = backup.snapshot(f)
    assert bp1.name == "x.md.bak.20260101-120000"
    assert bp2.name == "x.md.bak.20260101-120000-1"
    assert bp3.name == "x.md.bak.20260101-120000-2"
    # all three preserved
    assert bp1.exists() and bp2.exists() and bp3.exists()


def test_collision_retention_tiebreak(life_tree, monkeypatch):
    """With equal timestamps, keep=N caps count and each backup is unique.

    Note: after retention trims older entries, freed probe slots (e.g. the
    bare-base name) become reusable, so the -N suffixes are not strictly
    monotonic. The stable invariants are (a) exactly `keep` backups remain,
    (b) their names are unique, and (c) the newest-first sort is stable.
    """
    f = life_tree / "x.md"
    f.write_text("body")
    monkeypatch.setattr(backup, "_timestamp", lambda: "20260101-000000")
    for _ in range(6):
        backup.snapshot(f, keep=4)
    backups = backup.list_backups(f)
    assert len(backups) == 4
    names = [b.name for b in backups]
    assert len(set(names)) == 4  # unique
    # Sort is deterministic newest-first across repeated calls
    again = [b.name for b in backup.list_backups(f)]
    assert names == again


# ============================================================== list/latest


def test_list_backups_empty(life_tree):
    f = life_tree / "none.md"
    f.write_text("")
    assert backup.list_backups(f) == []


def test_latest_backup_returns_none_when_empty(life_tree):
    f = life_tree / "none.md"
    f.write_text("")
    assert backup.latest_backup(f) is None


def test_latest_backup_returns_newest(life_tree, monkeypatch):
    f = life_tree / "x.md"
    f.write_text("body")
    monkeypatch.setattr(backup, "_timestamp", lambda: "20260101-120000")
    bp1 = backup.snapshot(f)
    bp2 = backup.snapshot(f)
    latest = backup.latest_backup(f)
    assert latest == bp2
    assert latest != bp1


# ================================================================= restore


def test_restore_latest(life_tree, monkeypatch):
    f = life_tree / "r.md"
    f.write_text("original")
    monkeypatch.setattr(backup, "_timestamp", lambda: "20260101-120000")
    backup.snapshot(f)
    f.write_text("corrupted")
    restored_from = backup.restore(f)
    assert restored_from is not None
    assert f.read_text() == "original"


def test_restore_with_no_backups(life_tree):
    f = life_tree / "r.md"
    f.write_text("x")
    assert backup.restore(f) is None


def test_restore_recreates_deleted_original(life_tree, monkeypatch):
    f = life_tree / "gone.md"
    f.write_text("payload")
    monkeypatch.setattr(backup, "_timestamp", lambda: "20260101-120000")
    backup.snapshot(f)
    f.unlink()
    restored_from = backup.restore(f)
    assert restored_from is not None
    assert f.exists()
    assert f.read_text() == "payload"


def test_restore_to_specific_timestamp(life_tree, monkeypatch):
    f = life_tree / "v.md"
    f.write_text("gen1")
    monkeypatch.setattr(backup, "_timestamp", lambda: "20260101-120000")
    backup.snapshot(f)
    f.write_text("gen2")
    monkeypatch.setattr(backup, "_timestamp", lambda: "20260101-130000")
    backup.snapshot(f)
    f.write_text("gen3")

    # Restore the first backup explicitly
    backup.restore(f, to="20260101-120000")
    assert f.read_text() == "gen1"


def test_restore_to_unknown_timestamp_raises(life_tree, monkeypatch):
    f = life_tree / "v.md"
    f.write_text("x")
    monkeypatch.setattr(backup, "_timestamp", lambda: "20260101-120000")
    backup.snapshot(f)
    with pytest.raises(FileNotFoundError):
        backup.restore(f, to="20991231-235959")


# ================================================================= symlinks


def test_symlink_inside_life_dir_backs_up_target_bytes(life_tree):
    target = life_tree / "People" / "alpha" / "summary.md"
    target.parent.mkdir(parents=True)
    target.write_text("target content")

    link = life_tree / "link.md"
    link.symlink_to(target)

    bp = backup.snapshot(link)
    assert bp.read_text() == "target content"


def test_symlink_escaping_life_dir_raises(life_tree, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("SECRET")

    link = life_tree / "evil.md"
    link.symlink_to(outside)

    with pytest.raises(ValueError, match="escaping LIFE_DIR"):
        backup.snapshot(link)


def test_symlink_chain_escaping_raises(life_tree, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("PASSWD")

    mid = life_tree / "mid.md"
    mid.symlink_to(secret)

    outer = life_tree / "outer.md"
    outer.symlink_to(mid)

    with pytest.raises(ValueError, match="escaping LIFE_DIR"):
        backup.snapshot(outer)


# ================================================================= hardlinks


def test_hardlink_source_is_copied_not_linked(life_tree):
    original = life_tree / "orig.md"
    original.write_text("body")
    hardlink = life_tree / "hardlink.md"
    os.link(original, hardlink)
    assert hardlink.stat().st_nlink == 2

    bp = backup.snapshot(original)
    # Mutating the backup should NOT affect the original
    bp.write_text("mutated")
    assert original.read_text() == "body"


# =============================================================== atomicity


def test_atomic_write_cleans_partial_on_failure(life_tree, monkeypatch):
    f = life_tree / "atomic.md"
    f.write_text("body")

    def broken_replace(src, dst):
        # Simulate ENOSPC mid-replace
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(os, "replace", broken_replace)

    with pytest.raises(OSError):
        backup.snapshot(f)

    # No stray .part files
    leftovers = [p for p in life_tree.iterdir() if p.name.endswith(".part")]
    assert leftovers == []


def test_caller_must_not_mutate_after_snapshot_failure(life_tree, monkeypatch):
    """Smoke test of the caller contract: snapshot raises -> caller aborts."""
    f = life_tree / "a.md"
    f.write_text("original")

    def raising(src, dst):
        raise OSError("mock failure")
    monkeypatch.setattr(shutil, "copy2", raising)

    with pytest.raises(OSError):
        backup.snapshot(f)
    assert f.read_text() == "original"
    # Original file untouched, so a downstream rewriter can safely bail.


# =============================================================== mode bits


def test_copystat_preserves_mode(life_tree):
    f = life_tree / "mode.md"
    f.write_text("body")
    os.chmod(f, 0o640)
    bp = backup.snapshot(f)
    assert stat.S_IMODE(bp.stat().st_mode) == 0o640


# ================================================================= CLI


def test_cli_snapshot_then_list_then_restore(life_tree, monkeypatch):
    f = life_tree / "cli.md"
    f.write_text("before")
    env = {**os.environ, "LIFE_DIR": str(life_tree)}

    # snapshot
    r = subprocess.run(
        [sys.executable, str(CANONICAL / "backup.py"), "snapshot", str(f)],
        env=env, capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert ".bak." in r.stdout

    # list
    r = subprocess.run(
        [sys.executable, str(CANONICAL / "backup.py"), "list", str(f)],
        env=env, capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert ".bak." in r.stdout

    # corrupt and restore
    f.write_text("corrupted")
    r = subprocess.run(
        [sys.executable, str(CANONICAL / "backup.py"), "restore", str(f)],
        env=env, capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert f.read_text() == "before"


def test_cli_restore_no_backups_exits_1(life_tree):
    f = life_tree / "empty.md"
    f.write_text("x")
    env = {**os.environ, "LIFE_DIR": str(life_tree)}
    r = subprocess.run(
        [sys.executable, str(CANONICAL / "backup.py"), "restore", str(f)],
        env=env, capture_output=True, text=True,
    )
    assert r.returncode == 1
