#!/usr/bin/env python3
"""Decay stale facts in items.json files across all entities.

Usage:
    python3 decay_facts.py              # Run decay on all entities
    python3 decay_facts.py --dry-run    # Report what would change without modifying files
    python3 decay_facts.py --backfill   # Add last_seen field to facts missing it

Environment:
    LIFE_DIR            — path to ~/life/ (default: ~/life)
    CONSOLIDATION_DATE  — override today's date (for testing)
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
TODAY = os.environ.get("CONSOLIDATION_DATE", str(date.today()))
DRY_RUN = "--dry-run" in sys.argv
BACKFILL = "--backfill" in sys.argv
BACKFILL_TEMPORAL = "--backfill-temporal" in sys.argv

# Decay thresholds (days)
STALE_THRESHOLD = 14
ARCHIVE_THRESHOLD = 30
ESTABLISHED_STALE_THRESHOLD = 30
ESTABLISHED_ARCHIVE_THRESHOLD = 60

# Temporal facts (current states that change) decay faster
TEMPORAL_STALE_THRESHOLD = 7
TEMPORAL_ARCHIVE_THRESHOLD = 14
TEMPORAL_EST_STALE_THRESHOLD = 14
TEMPORAL_EST_ARCHIVE_THRESHOLD = 30

ENTITY_PATTERNS = [
    "Projects/*/items.json",
    "People/*/items.json",
    "Companies/*/items.json",
]


def decay_entity(items_path: Path, today: date) -> int:
    """Decay facts in a single items.json. Returns count of decayed facts."""
    try:
        items = json.loads(items_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        return 0

    if not isinstance(items, list):
        return 0

    decayed = 0
    for item in items:
        confidence = item.get("confidence", "single")
        if confidence in ("archived", "stale", "superseded"):
            continue

        last_seen = item.get("last_seen", item.get("date", ""))
        if not last_seen:
            continue
        try:
            last_seen_date = date.fromisoformat(last_seen)
        except (ValueError, TypeError):
            continue

        days_since = (today - last_seen_date).days
        mentions = item.get("mentions", 1)

        # Temporal facts (current states) decay faster; established facts get extra grace
        is_temporal = item.get("temporal", False)
        is_established = confidence == "established" and mentions >= 4

        if is_temporal and is_established:
            stale_thresh = TEMPORAL_EST_STALE_THRESHOLD
            archive_thresh = TEMPORAL_EST_ARCHIVE_THRESHOLD
        elif is_temporal:
            stale_thresh = TEMPORAL_STALE_THRESHOLD
            archive_thresh = TEMPORAL_ARCHIVE_THRESHOLD
        elif is_established:
            stale_thresh = ESTABLISHED_STALE_THRESHOLD
            archive_thresh = ESTABLISHED_ARCHIVE_THRESHOLD
        else:
            stale_thresh = STALE_THRESHOLD
            archive_thresh = ARCHIVE_THRESHOLD

        if days_since >= archive_thresh:
            item["confidence"] = "archived"
            decayed += 1
        elif days_since >= stale_thresh and confidence in ("single", "confirmed", "established"):
            item["confidence"] = "stale"
            decayed += 1

    if decayed and not DRY_RUN:
        items_path.write_text(json.dumps(items, indent=2), encoding="utf-8")

    return decayed


def backfill_entity(items_path: Path) -> int:
    """Add last_seen = date to facts missing it. Returns count of backfilled facts."""
    try:
        items = json.loads(items_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        return 0

    if not isinstance(items, list):
        return 0

    changed = 0
    for item in items:
        if BACKFILL and "last_seen" not in item:
            item["last_seen"] = item.get("date", TODAY)
            changed += 1
        if BACKFILL_TEMPORAL and "temporal" not in item:
            item["temporal"] = False
            changed += 1

    if changed and not DRY_RUN:
        items_path.write_text(json.dumps(items, indent=2), encoding="utf-8")

    return changed


def main():
    today = date.fromisoformat(TODAY)
    total = 0

    for pattern in ENTITY_PATTERNS:
        for items_path in sorted(LIFE_DIR.glob(pattern)):
            entity = items_path.parent.name

            if BACKFILL or BACKFILL_TEMPORAL:
                count = backfill_entity(items_path)
                if count:
                    prefix = "(dry) " if DRY_RUN else ""
                    print(f"[decay] {prefix}{entity}: {count} facts backfilled")
                    total += count
            else:
                count = decay_entity(items_path, today)
                if count:
                    prefix = "(dry) " if DRY_RUN else ""
                    print(f"[decay] {prefix}{entity}: {count} facts decayed")
                    total += count

    action = "backfilled" if (BACKFILL or BACKFILL_TEMPORAL) else "decayed"
    prefix = "(dry) " if DRY_RUN else ""
    print(f"[decay] {prefix}Total: {total} facts {action}")


if __name__ == "__main__":
    main()
