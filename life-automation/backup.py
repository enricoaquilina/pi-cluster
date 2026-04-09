"""Phase 8.0.3 — versioned backup utility.

Semantics (see plan v3 §8.0.3):

* Backup filename: ``<name>.bak.<YYYYMMDD-HHMMSS>[-<N>]`` in ``path.parent``.
* Collision rule: probe ``-1, -2, ...`` via ``O_CREAT|O_EXCL``, up to 100
  attempts. Deterministic under concurrent callers.
* Retention: per-file, keep ``keep`` newest by ``(timestamp, -N)``.
* **Symlink security**: ``lstat`` first. If ``path`` is a symlink and its
  resolved target is outside ``$LIFE_DIR``, raise ``ValueError``.
* Hardlink safety: use ``shutil.copy2`` (not rename/link).
* Atomic write: write to ``<final>.part`` in the same directory, fsync, then
  ``os.replace``. Catch ``ENOSPC`` → clean up partial, re-raise.
* Empty files are backed up (zero-byte result).
* Missing original at restore time: parent dirs are recreated.
* **Caller contract**: if ``snapshot()`` raises, the caller MUST NOT mutate
  the original file.
"""
from __future__ import annotations

import errno
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Optional

__all__ = [
    "snapshot",
    "latest_backup",
    "list_backups",
    "restore",
]

BACKUP_SUFFIX_RE = ".bak."


def _life_dir() -> Path:
    return Path(os.environ.get("LIFE_DIR", str(Path.home() / "life"))).resolve()


def _timestamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _assert_inside_life_dir(target: Path) -> None:
    """Refuse to operate on a symlink whose target escapes $LIFE_DIR."""
    life = _life_dir()
    try:
        resolved = target.resolve()
    except OSError as exc:
        raise ValueError(f"cannot resolve path: {target}: {exc}") from exc
    try:
        resolved.relative_to(life)
    except ValueError:
        raise ValueError(
            f"refusing to back up symlink escaping LIFE_DIR: "
            f"{target} -> {resolved} (LIFE_DIR={life})"
        )


def _probe_unique_name(parent: Path, base: str) -> Path:
    """Find an unused ``base`` or ``base-N`` name via O_CREAT|O_EXCL.

    Returns the Path of a freshly-created empty file.
    """
    candidates = [parent / base] + [parent / f"{base}-{i}" for i in range(1, 101)]
    for candidate in candidates:
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        os.close(fd)
        return candidate
    raise RuntimeError(
        f"could not find unique backup name after 100 attempts for {parent / base}"
    )


def _copy_atomic(src: Path, dst: Path) -> None:
    """Copy ``src`` bytes into ``dst`` atomically via a tempfile + replace."""
    part = dst.with_name(dst.name + ".part")
    try:
        # shutil.copy2 handles permissions + mtime on the target.
        shutil.copy2(src, part)
        # fsync the file contents before rename to survive a crash.
        fd = os.open(part, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(part, dst)
    except OSError as exc:
        # Clean up the partial if something went wrong (e.g. ENOSPC)
        try:
            if part.exists():
                part.unlink()
        except OSError:
            pass
        # Also remove the pre-created placeholder if we never wrote over it
        if dst.exists() and dst.stat().st_size == 0 and exc.errno == errno.ENOSPC:
            try:
                dst.unlink()
            except OSError:
                pass
        raise


# ----------------------------------------------------------------- public API


def snapshot(path: Path, keep: int = 5) -> Path:
    """Create a timestamped backup of ``path`` and trim retention.

    Returns the resulting backup file path.
    """
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        raise FileNotFoundError(path)

    # Symlink containment check (raises ValueError on escape)
    if path.is_symlink():
        _assert_inside_life_dir(path)

    # Backup file sits next to the source with a timestamp suffix
    base = f"{path.name}{BACKUP_SUFFIX_RE}{_timestamp()}"
    placeholder = _probe_unique_name(path.parent, base)

    try:
        _copy_atomic(path, placeholder)
    except OSError:
        # Clean up placeholder on any failure
        try:
            placeholder.unlink()
        except OSError:
            pass
        raise

    # Retention trim
    _trim_backups(path, keep=keep)
    return placeholder


def list_backups(path: Path) -> list[Path]:
    """Return all backups for ``path``, newest first.

    Sorted by ``(timestamp_string, suffix_N)`` in descending order so ties
    caused by same-second collisions are broken by ``-N`` suffix.
    """
    path = Path(path)
    parent = path.parent
    if not parent.is_dir():
        return []
    prefix = f"{path.name}{BACKUP_SUFFIX_RE}"

    def sort_key(p: Path) -> tuple[str, int]:
        tail = p.name[len(prefix):]
        if "-" in tail:
            ts, n = tail.rsplit("-", 1)
            try:
                return (ts, int(n))
            except ValueError:
                return (tail, 0)
        return (tail, 0)

    candidates = [
        p for p in parent.iterdir()
        if p.name.startswith(prefix) and not p.name.endswith(".part")
    ]
    candidates.sort(key=sort_key, reverse=True)
    return candidates


def latest_backup(path: Path) -> Optional[Path]:
    backups = list_backups(path)
    return backups[0] if backups else None


def _trim_backups(path: Path, *, keep: int) -> None:
    backups = list_backups(path)
    for extra in backups[keep:]:
        try:
            extra.unlink()
        except OSError:
            pass


def restore(path: Path, *, to: Optional[str] = None) -> Optional[Path]:
    """Restore ``path`` from its most recent backup (or a specific timestamp).

    Returns the backup path that was used, or ``None`` if no backups exist.

    ``to`` matches against the timestamp portion (with optional ``-N``) and
    accepts either the bare timestamp or the full ``YYYYMMDD-HHMMSS`` form.
    """
    path = Path(path)
    backups = list_backups(path)
    if not backups:
        return None
    chosen: Optional[Path] = None
    if to is None:
        chosen = backups[0]
    else:
        prefix = f"{path.name}{BACKUP_SUFFIX_RE}{to}"
        for b in backups:
            if b.name == prefix or b.name.startswith(prefix + "-"):
                chosen = b
                break
        if chosen is None:
            raise FileNotFoundError(f"no backup matching --to {to!r} for {path}")

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".restore.part")
    shutil.copy2(chosen, tmp)
    os.replace(tmp, path)
    return chosen


# ---------------------------------------------------------------- CLI entry


def _main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: backup.py {snapshot|restore|list} <path> [--to TS]", file=sys.stderr)
        return 2
    cmd = argv[0]
    target = Path(argv[1])
    if cmd == "snapshot":
        bp = snapshot(target)
        print(bp)
        return 0
    if cmd == "list":
        for b in list_backups(target):
            print(b)
        return 0
    if cmd == "restore":
        to: Optional[str] = None
        if "--to" in argv:
            to = argv[argv.index("--to") + 1]
        restored = restore(target, to=to)
        if restored is None:
            print(f"no backups found for {target}", file=sys.stderr)
            return 1
        print(restored)
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
