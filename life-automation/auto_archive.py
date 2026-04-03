#!/usr/bin/env python3
"""
Auto-archive completed/inactive projects.
Scans Projects/ for entities with status: completed or status: archived in frontmatter,
moves them to Archives/Projects/{slug}/.
"""
import os
import re
import shutil
import sys
from pathlib import Path

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
DRY_RUN = "--dry-run" in sys.argv

def get_status(summary_path: Path) -> str | None:
    """Extract status from YAML frontmatter."""
    try:
        content = summary_path.read_text(encoding="utf-8")
        match = re.search(r'^status:\s*(\S+)', content, re.MULTILINE)
        return match.group(1) if match else None
    except (FileNotFoundError, OSError):
        return None

def archive_projects() -> list[str]:
    """Move completed/archived projects to Archives/."""
    archived = []
    projects_dir = LIFE_DIR / "Projects"
    archives_dir = LIFE_DIR / "Archives" / "Projects"

    if not projects_dir.exists():
        return archived

    for proj_dir in sorted(projects_dir.iterdir()):
        if not proj_dir.is_dir() or proj_dir.name.startswith("_"):
            continue
        summary = proj_dir / "summary.md"
        status = get_status(summary)
        if status in ("completed", "archived"):
            dest = archives_dir / proj_dir.name
            if dest.exists():
                print(f"[archive] Skip — already archived: {proj_dir.name}", file=sys.stderr)
                continue
            if not DRY_RUN:
                archives_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(proj_dir), str(dest))
            print(f"[archive] {'(dry) ' if DRY_RUN else ''}Archived: {proj_dir.name}")
            archived.append(proj_dir.name)

    return archived

if __name__ == "__main__":
    result = archive_projects()
    print(f"[archive] Done — {len(result)} projects archived")
