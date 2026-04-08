#!/usr/bin/env python3
"""Lint ~/life/ knowledge base for consistency issues.

Checks:
  1. Orphan relationships — entities in relationships.json without entity dirs
  2. Stale relationships — edges with last_seen > 30 days
  3. Empty entity dirs — dirs in Projects/People/Companies without summary.md

Usage:
    python3 lint_knowledge.py              # Check all
    python3 lint_knowledge.py --dry-run    # Same (lint is read-only by default)
    python3 lint_knowledge.py --json       # Output structured JSON

Environment:
    LIFE_DIR            — path to ~/life/ (default: ~/life)
    CONSOLIDATION_DATE  — override today's date (for testing)
"""
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
TODAY = os.environ.get("CONSOLIDATION_DATE", "")
if not TODAY:
    TODAY = str(date.today())

JSON_OUTPUT = "--json" in sys.argv

ENTITY_TYPES = ["Projects", "People", "Companies"]
TYPE_MAP = {"person": "People", "project": "Projects", "company": "Companies"}

findings: list[dict] = []


def finding(category: str, message: str, path: str = "") -> None:
    findings.append({"category": category, "message": message, "path": path})
    if not JSON_OUTPUT:
        print(f"[lint] {category}: {message}")


def check_orphan_relationships(relationships: list[dict]) -> None:
    """Check that all entities in relationships.json have entity dirs."""
    seen_slugs: set[str] = set()
    for edge in relationships:
        for role in ("from", "to"):
            slug = edge.get(role, "")
            role_type = edge.get(f"{role}_type", "")
            parent = TYPE_MAP.get(role_type, "")
            if not slug or not parent or slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            entity_dir = LIFE_DIR / parent / slug
            if not entity_dir.is_dir():
                finding(
                    "ORPHAN",
                    f"'{slug}' ({role_type}) in relationships.json has no {parent}/{slug}/ dir",
                    str(entity_dir),
                )


def check_stale_relationships(relationships: list[dict]) -> None:
    """Check for relationships not seen in 30+ days."""
    try:
        cutoff = str(date.fromisoformat(TODAY) - timedelta(days=30))
    except ValueError:
        return
    for edge in relationships:
        last_seen = edge.get("last_seen", "")
        if last_seen and last_seen < cutoff:
            finding(
                "STALE",
                f"{edge.get('from', '?')} → {edge.get('to', '?')} last seen {last_seen}",
            )


def check_empty_entity_dirs() -> None:
    """Check for entity dirs without summary.md."""
    for entity_type in ENTITY_TYPES:
        entity_dir = LIFE_DIR / entity_type
        if not entity_dir.is_dir():
            continue
        for d in sorted(entity_dir.iterdir()):
            if not d.is_dir():
                continue
            if d.name.startswith("_") or d.name.startswith("."):
                continue
            if not (d / "summary.md").exists():
                finding(
                    "EMPTY_DIR",
                    f"{entity_type}/{d.name}/ has no summary.md",
                    str(d),
                )


def main() -> None:
    # Load relationships
    rel_path = LIFE_DIR / "relationships.json"
    relationships: list[dict] = []
    try:
        relationships = json.loads(rel_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print("[lint] SKIP: relationships.json not found")
    except json.JSONDecodeError as e:
        print(f"[lint] SKIP: relationships.json malformed ({e})")

    if relationships:
        check_orphan_relationships(relationships)
        check_stale_relationships(relationships)

    check_empty_entity_dirs()

    if JSON_OUTPUT:
        json.dump(findings, sys.stdout, indent=2)
        print()
    elif not findings:
        print("[lint] All clean — no issues found")
    else:
        print(f"[lint] {len(findings)} issue(s) found")


if __name__ == "__main__":
    main()
