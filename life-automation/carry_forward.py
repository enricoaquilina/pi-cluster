#!/usr/bin/env python3
"""
Carry forward unchecked pending items from yesterday's daily note to today's.
Designed to run at session start (called from CLAUDE.md protocol or as a standalone script).
"""
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
DRY_RUN = "--dry-run" in sys.argv

def daily_note_path(d: date) -> Path:
    return LIFE_DIR / "Daily" / str(d.year) / f"{d.month:02d}" / f"{d}.md"

def get_open_items(content: str) -> list[str]:
    """Extract unchecked items (- [ ]) from Pending Items section."""
    section = ""
    in_section = False
    for line in content.split("\n"):
        if line.strip().startswith("## Pending"):
            in_section = True
            continue
        if in_section and line.strip().startswith("## "):
            break
        if in_section:
            section += line + "\n"

    items = []
    for line in section.split("\n"):
        stripped = line.strip()
        if stripped.startswith("- [ ]") and len(stripped) > 5:
            items.append(stripped)
    return items

def carry_forward() -> int:
    """Copy unchecked items from yesterday to today's pending section."""
    today = date.fromisoformat(os.environ.get("CONSOLIDATION_DATE", str(date.today())))
    yesterday = today - timedelta(days=1)

    yesterday_path = daily_note_path(yesterday)
    today_path = daily_note_path(today)

    if not yesterday_path.exists():
        print("[carry] No yesterday's note — nothing to carry forward")
        return 0
    if not today_path.exists():
        print("[carry] No today's note — create it first")
        return 0

    yesterday_content = yesterday_path.read_text(encoding="utf-8")
    open_items = get_open_items(yesterday_content)

    if not open_items:
        print("[carry] No open items from yesterday")
        return 0

    today_content = today_path.read_text(encoding="utf-8")

    # Check if items are already carried forward (idempotent)
    new_items = [item for item in open_items if item not in today_content]

    if not new_items:
        print(f"[carry] All {len(open_items)} items already in today's note")
        return 0

    marker = "## Pending"
    if marker not in today_content:
        print("[carry] No Pending section in today's note")
        return 0

    if not DRY_RUN:
        # Insert items after the Pending header and any existing "- [ ]" line
        lines = today_content.split("\n")
        new_lines = []
        inserted = False
        for i, line in enumerate(lines):
            new_lines.append(line)
            if not inserted and line.strip().startswith("## Pending"):
                # Find the first empty line or "- [ ]" placeholder after header
                # and insert after it
                if i + 1 < len(lines) and lines[i + 1].strip() == "- [ ]":
                    new_lines.append(lines[i + 1])  # keep placeholder
                    new_lines.append(f"<!-- Carried from {yesterday} -->")
                    new_lines.extend(new_items)
                    inserted = True
                    # Skip the next line (already added)
                    lines[i + 1] = ""
                else:
                    new_lines.append(f"<!-- Carried from {yesterday} -->")
                    new_lines.extend(new_items)
                    inserted = True

        today_path.write_text("\n".join(new_lines), encoding="utf-8")

    print(f"[carry] {'(dry) ' if DRY_RUN else ''}{len(new_items)} items carried from {yesterday}")
    return len(new_items)

if __name__ == "__main__":
    carry_forward()
