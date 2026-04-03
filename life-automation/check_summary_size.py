#!/usr/bin/env python3
"""Check entity summary.md files for size limits.

Soft warning at 100 lines, escalation pending item at 150 lines.

Usage:
    python3 check_summary_size.py              # Check all entities
    python3 check_summary_size.py --dry-run    # Report only, no writes

Environment:
    LIFE_DIR            — path to ~/life/ (default: ~/life)
    CONSOLIDATION_DATE  — override today's date (for testing)
"""
import json
import os
import sys
from pathlib import Path

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
TODAY = os.environ.get("CONSOLIDATION_DATE", "")
if not TODAY:
    from datetime import date
    TODAY = str(date.today())
DRY_RUN = "--dry-run" in sys.argv

SOFT_LIMIT = 100
HARD_LIMIT = 150

ENTITY_DIRS = ["Projects", "People", "Companies"]

PENDING_FACT = "summary.md exceeds 150 lines — needs manual pruning"


def check_entity(summary_path: Path) -> int:
    """Check a single summary.md. Returns line count."""
    lines = summary_path.read_text(encoding="utf-8").splitlines()
    count = len(lines)
    entity = summary_path.parent.name

    if count > HARD_LIMIT:
        print(f"[summary-check] ESCALATION: {entity} summary is {count} lines (hard limit: {HARD_LIMIT})", file=sys.stderr)
        if not DRY_RUN:
            _add_pending(summary_path.parent / "items.json", entity)
    elif count > SOFT_LIMIT:
        print(f"[summary-check] WARNING: {entity} summary is {count} lines (soft limit: {SOFT_LIMIT})", file=sys.stderr)

    return count


def _add_pending(items_path: Path, entity: str) -> None:
    """Append a pruning pending item if not already present."""
    items = []
    if items_path.exists():
        try:
            items = json.loads(items_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            items = []

    # Dedup: skip if identical pending already exists
    for item in items:
        if item.get("fact") == PENDING_FACT and item.get("confidence") not in ("superseded", "archived"):
            return

    items.append({
        "date": TODAY,
        "fact": PENDING_FACT,
        "category": "pending",
        "source": "auto/summary-check",
        "confidence": "single",
        "mentions": 1,
        "last_seen": TODAY,
        "temporal": True,
    })
    items_path.write_text(json.dumps(items, indent=2), encoding="utf-8")
    print(f"[summary-check] Added pruning pending item for {entity}")


def main() -> None:
    for entity_dir in ENTITY_DIRS:
        base = LIFE_DIR / entity_dir
        if not base.exists():
            continue
        for child in sorted(base.iterdir()):
            if child.name.startswith("_"):
                continue
            summary = child / "summary.md"
            if summary.exists():
                check_entity(summary)

    print(f"[summary-check] Done")


if __name__ == "__main__":
    main()
