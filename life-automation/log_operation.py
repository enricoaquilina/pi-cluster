#!/usr/bin/env python3
"""Unified operation log for ~/life knowledge base.

Appends to ~/life/log.md in a parseable format matching Karpathy's log.md pattern.

Usage:
    # From Python:
    from log_operation import log_operation
    log_operation("consolidate", "2 facts added, 0 entities created")

    # From CLI:
    python3 log_operation.py consolidate "2 facts added"
    python3 log_operation.py session "[research] Phase 6 implementation"
    python3 log_operation.py ingest "pi-cluster-readme.md → 11 facts"

Environment:
    LIFE_DIR  — path to ~/life/ (default: ~/life)
"""
import os
import sys
from datetime import datetime
from pathlib import Path

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
LOG_PATH = LIFE_DIR / "log.md"
MAX_LINES = 10_000  # Rotate after this many lines


def log_operation(op_type: str, summary: str, details: str = "") -> None:
    """Append an operation entry to log.md."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"## [{ts}] {op_type} | {summary}\n"
    if details:
        entry += f"{details}\n"

    # Check rotation
    if LOG_PATH.exists():
        line_count = sum(1 for _ in LOG_PATH.open(encoding="utf-8"))
        if line_count >= MAX_LINES:
            year = datetime.now().strftime("%Y")
            archive = LIFE_DIR / f"log-{year}.md"
            LOG_PATH.rename(archive)

    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(entry)


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: log_operation.py <type> <summary> [details]", file=sys.stderr)
        sys.exit(1)
    op_type = sys.argv[1]
    summary = sys.argv[2]
    details = sys.argv[3] if len(sys.argv) > 3 else ""
    log_operation(op_type, summary, details)


if __name__ == "__main__":
    main()
